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
NEW_HUB_CONTEXT=""
OLD_HUB_CONTEXT=""
SKIP_GITOPS_CHECK=0
ARGOCD_CHECK=0

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
        --skip-gitops-check)
            SKIP_GITOPS_CHECK=1
            shift
            ;;
        --argocd-check)
            ARGOCD_CHECK=1
            shift
            ;;
        --help|-h)
            echo "Usage: $0 --new-hub-context <context> [--old-hub-context <context>] [--skip-gitops-check] [--argocd-check]"
            echo ""
            echo "Options:"
            echo "  --new-hub-context     Kubernetes context for new active hub (required)"
            echo "  --old-hub-context     Kubernetes context for old primary hub (optional)"
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
if [[ -z "$NEW_HUB_CONTEXT" ]]; then
    echo -e "${RED}Error: --new-hub-context is required${NC}"
    echo "Use --help for usage information"
    exit "$EXIT_INVALID_ARGS"
fi

# Main validation
echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║   ACM Switchover Post-flight Verification                 ║"
echo "╚════════════════════════════════════════════════════════════╝"
print_script_version "postflight-check.sh"
echo ""
echo "New Hub:        $NEW_HUB_CONTEXT"
if [[ -n "$OLD_HUB_CONTEXT" ]]; then
    echo "Old Hub:        $OLD_HUB_CONTEXT (for comparison)"
fi
echo ""

# Check 0: Verify CLI tools
section_header "0. Checking CLI Tools"
detect_cluster_cli

# Check 1: Verify restore completed
section_header "1. Checking Restore Status"

# Try to find passive sync restore by syncRestoreWithNewBackups=true first
PASSIVE_SYNC_RESTORE=$(oc --context="$NEW_HUB_CONTEXT" get $RES_RESTORE -n "$BACKUP_NAMESPACE" -o json 2>/dev/null | \
    jq -r '.items[] | select(.spec.syncRestoreWithNewBackups == true) | "\(.metadata.name) \(.status.phase // "unknown") \(.metadata.creationTimestamp)"' | head -1 || true)

if [[ -n "$PASSIVE_SYNC_RESTORE" ]]; then
    read -r RESTORE_NAME RESTORE_PHASE RESTORE_TIME <<< "$PASSIVE_SYNC_RESTORE"
    IS_PASSIVE_SYNC=true
else
    # Fallback: get the most recent restore (sort by creation timestamp)
    read -r RESTORE_NAME RESTORE_PHASE RESTORE_TIME <<< "$(oc --context="$NEW_HUB_CONTEXT" get $RES_RESTORE -n "$BACKUP_NAMESPACE" --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name} {.items[-1].status.phase} {.items[-1].metadata.creationTimestamp}' 2>/dev/null || true)"
    IS_PASSIVE_SYNC=false
fi

# Check if BackupSchedule is enabled (which deletes Restore objects)
BACKUP_SCHEDULE_ENABLED=$(oc --context="$NEW_HUB_CONTEXT" get $RES_BACKUP_SCHEDULE -n "$BACKUP_NAMESPACE" -o jsonpath='{.items[0].spec.paused}' 2>/dev/null || echo "")

if [[ -n "$RESTORE_NAME" ]]; then
    if [[ "$RESTORE_PHASE" == "Finished" ]] || [[ "$RESTORE_PHASE" == "Completed" ]]; then
        check_pass "Latest restore '$RESTORE_NAME' completed successfully (Phase: $RESTORE_PHASE, Created: $RESTORE_TIME)"
        
        # Identify if this is a passive sync restore
        if [[ "$IS_PASSIVE_SYNC" == "true" ]]; then
            echo -e "       (Identified as passive sync restore via spec.syncRestoreWithNewBackups=true)"
        fi

        # Check age of restore
        if [[ -n "$RESTORE_TIME" ]]; then
            RESTORE_EPOCH=$(date -d "$RESTORE_TIME" +%s 2>/dev/null || echo "0")
            CURRENT_EPOCH=$(date +%s)
            AGE_SECONDS=$((CURRENT_EPOCH - RESTORE_EPOCH))
            
            # If older than threshold
            if [[ $AGE_SECONDS -gt $RESTORE_AGE_WARNING_SECONDS ]]; then
                 check_warn "Latest restore is older than 1 hour ($((AGE_SECONDS / 60)) mins ago). Ensure this is the switchover restore."
            fi
        fi
    elif [[ "$RESTORE_PHASE" == "Enabled" ]]; then
        check_warn "Latest restore '$RESTORE_NAME' is Enabled (passive sync may still be running)"
    else
        check_fail "Latest restore '$RESTORE_NAME' in unexpected state: $RESTORE_PHASE"
        RESTORE_MESSAGE=$(oc --context="$NEW_HUB_CONTEXT" get $RES_RESTORE "$RESTORE_NAME" -n "$BACKUP_NAMESPACE" -o jsonpath='{.status.lastMessage}' 2>/dev/null || true)
        if [[ -n "$RESTORE_MESSAGE" ]]; then
            echo -e "${RED}       Restore message: $RESTORE_MESSAGE${NC}"
        fi
        BSL_CONDITIONS=$(oc --context="$NEW_HUB_CONTEXT" get $RES_BSL -n "$BACKUP_NAMESPACE" -o json 2>/dev/null | \
            jq -r '.items[0].status.conditions // [] | map("\(.type)=\(.status) reason=\(.reason // "n/a") msg=\(.message // "n/a")") | join("; ")' || true)
        if [[ -n "$BSL_CONDITIONS" ]]; then
            echo -e "${RED}       BSL conditions: $BSL_CONDITIONS${NC}"
        fi
        echo -e "${RED}       Unavailable BSL means restores cannot proceed${NC}"
    fi
elif [[ "$BACKUP_SCHEDULE_ENABLED" == "false" ]] || [[ -z "$BACKUP_SCHEDULE_ENABLED" ]]; then
    # No restore objects but BackupSchedule is enabled - this is expected behavior
    # When BackupSchedule is enabled, OADP cleans up Restore objects
    check_pass "No restore objects found (expected: BackupSchedule is enabled and cleaned up Restore resources)"
    echo -e "       ${YELLOW}Note: OADP deletes Restore objects when BackupSchedule is active${NC}"
else
    check_fail "No restore resources found on new hub"
fi

# Check 2: Verify ManagedClusters are connected
section_header "2. Checking ManagedCluster Status"

TOTAL_CLUSTERS=$(oc --context="$NEW_HUB_CONTEXT" get $RES_MANAGED_CLUSTER --no-headers 2>/dev/null | grep -c -v "$LOCAL_CLUSTER_NAME" || true)
if [[ $TOTAL_CLUSTERS -gt 0 ]]; then
    check_pass "Found $TOTAL_CLUSTERS managed cluster(s) (excluding $LOCAL_CLUSTER_NAME)"
    
    # Check Available status
    # Check Available status
    # Identify clusters that are NOT Available (single API call)
    # This correctly catches clusters with Available=False, Unknown, or missing status
    UNAVAILABLE_LIST=$(oc --context="$NEW_HUB_CONTEXT" get $RES_MANAGED_CLUSTER -o json 2>/dev/null | \
        jq -r --arg LOCAL "$LOCAL_CLUSTER_NAME" '.items[] | select(.metadata.name != $LOCAL) | select(
            ([.status.conditions[]? | select(.type=="ManagedClusterConditionAvailable" and .status=="True")] | length) == 0
        ) | .metadata.name')
    
    if [[ -z "$UNAVAILABLE_LIST" ]]; then
        check_pass "All $TOTAL_CLUSTERS cluster(s) show Available=True"
    else
        NUM_UNAVAILABLE=$(echo "$UNAVAILABLE_LIST" | grep -c -v "^$" || true)
        NUM_AVAILABLE=$((TOTAL_CLUSTERS - NUM_UNAVAILABLE))
        
        check_fail "Only $NUM_AVAILABLE of $TOTAL_CLUSTERS cluster(s) are Available"
        echo -e "${RED}       Unavailable clusters:${NC}"
        echo "$UNAVAILABLE_LIST" | sed 's/^/         - /'
    fi
    
    # Check Joined status
    JOINED_CLUSTERS=$(oc --context="$NEW_HUB_CONTEXT" get $RES_MANAGED_CLUSTER -o json 2>/dev/null | \
        jq -r --arg LOCAL "$LOCAL_CLUSTER_NAME" '.items[] | select(.metadata.name != $LOCAL) | select(.status.conditions[]? | select(.type=="ManagedClusterJoined" and .status=="True")) | .metadata.name' | wc -l)
    
    if [[ $JOINED_CLUSTERS -eq $TOTAL_CLUSTERS ]]; then
        check_pass "All $TOTAL_CLUSTERS cluster(s) show Joined=True"
    else
        check_warn "$JOINED_CLUSTERS of $TOTAL_CLUSTERS cluster(s) are Joined (some may still be connecting)"
    fi
    
    # Check for Pending Import
    PENDING_IMPORT=$(oc --context="$NEW_HUB_CONTEXT" get $RES_MANAGED_CLUSTER 2>/dev/null | grep -c "Pending Import" || true)
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

if oc --context="$NEW_HUB_CONTEXT" get namespace "$OBSERVABILITY_NAMESPACE" &> /dev/null; then
    check_pass "Observability namespace exists"

    # Check MCO CR status
    MCO_STATUS=$(oc --context="$NEW_HUB_CONTEXT" get $RES_MCO observability -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "Unknown")
    
    # Detect GitOps markers on MCO (cluster-scoped, no namespace)
    MCO_JSON=$(oc --context="$NEW_HUB_CONTEXT" get $RES_MCO observability -o json 2>/dev/null || echo "{}")
    if [[ -n "$MCO_JSON" && "$MCO_JSON" != "{}" ]]; then
        MCO_NAME=$(echo "$MCO_JSON" | jq -r '.metadata.name // "observability"')
        GITOPS_MARKERS=$(detect_gitops_markers "$MCO_JSON")
        if [[ -n "$GITOPS_MARKERS" ]]; then
            collect_gitops_markers "new-hub" "" "MultiClusterObservability" "$MCO_NAME" "$GITOPS_MARKERS"
        fi
    fi

    if [[ "$MCO_STATUS" == "True" ]]; then
        check_pass "MultiClusterObservability CR is Ready"
    else
        check_warn "MultiClusterObservability CR is not Ready (Status: $MCO_STATUS)"
    fi

    # Check critical pods using helper function
    CRITICAL_PODS=("$OBS_GRAFANA_POD" "$OBS_API_POD" "$OBS_THANOS_QUERY_POD")

    for pod_prefix in "${CRITICAL_PODS[@]}"; do
        POD_COUNT=$(get_running_pod_count "$NEW_HUB_CONTEXT" "$OBSERVABILITY_NAMESPACE" "app=${pod_prefix}" "$pod_prefix")
        if [[ $POD_COUNT -gt 0 ]]; then
            check_pass "${pod_prefix}: $POD_COUNT pod(s) running"
        else
            check_fail "${pod_prefix}: No running pods found"
        fi
    done

    # Check for any pods in error state
    ERROR_PODS=$(oc --context="$NEW_HUB_CONTEXT" get pods -n "$OBSERVABILITY_NAMESPACE" --no-headers 2>/dev/null | \
        grep -E -c "Error|CrashLoopBackOff|ImagePullBackOff" || true)
    if [[ $ERROR_PODS -eq 0 ]]; then
        check_pass "No pods in error state"
    else
        check_fail "$ERROR_PODS pod(s) in error state"
    fi

    # Check observatorium-api specifically (critical for metrics)
    OBSERVATORIUM_API_PODS=$(get_running_pod_count "$NEW_HUB_CONTEXT" "$OBSERVABILITY_NAMESPACE" "app.kubernetes.io/name=observatorium-api" "$OBS_API_POD")

    if [[ $OBSERVATORIUM_API_PODS -gt 0 ]]; then
        # Check if pods were recently restarted (should be after switchover)
        # Try to get start time using label, fallback to name
        RESTART_TIME=$(oc --context="$NEW_HUB_CONTEXT" get pods -n "$OBSERVABILITY_NAMESPACE" -l "app.kubernetes.io/name=observatorium-api" -o jsonpath='{.items[0].status.startTime}' 2>/dev/null || true)
        if [[ -z "$RESTART_TIME" ]]; then
             RESTART_TIME=$(oc --context="$NEW_HUB_CONTEXT" get pods -n "$OBSERVABILITY_NAMESPACE" --no-headers 2>/dev/null | grep "$OBS_API_POD" | head -n 1 | awk '{print "Unknown (Name match)"}')
        fi
        check_pass "observatorium-api pods running (started: $RESTART_TIME)"
    else
        check_fail "observatorium-api pods not running (critical for metrics)"
    fi
else
    check_pass "Observability not installed (optional component)"
fi

# Check 4: Verify Grafana metrics (if observability exists)
section_header "4. Checking Metrics Collection"

if oc --context="$NEW_HUB_CONTEXT" get namespace "$OBSERVABILITY_NAMESPACE" &> /dev/null; then
    # Get Grafana route
    GRAFANA_ROUTE=$(oc --context="$NEW_HUB_CONTEXT" get route grafana -n "$OBSERVABILITY_NAMESPACE" -o jsonpath='{.spec.host}' 2>/dev/null || echo "")
    
    if [[ -n "$GRAFANA_ROUTE" ]]; then
        check_pass "Grafana route accessible: https://$GRAFANA_ROUTE"
    else
        check_warn "Grafana route not found"
    fi
    
    # Check metrics-collector on managed clusters (sample check)
    if [[ $TOTAL_CLUSTERS -gt 0 ]]; then
        # This is informational - we can't easily check all managed clusters
        check_warn "Verify metrics-collector pods on managed clusters manually (oc get pods -n $OBSERVABILITY_ADDON_NAMESPACE)"
        echo -e "${YELLOW}       Wait 5-10 minutes after switchover for metrics to appear in Grafana${NC}"
    fi
fi

# Check 5: Verify BackupSchedule is enabled
section_header "5. Checking Backup Configuration"

BACKUP_SCHEDULE=$(oc --context="$NEW_HUB_CONTEXT" get $RES_BACKUP_SCHEDULE -n "$BACKUP_NAMESPACE" --no-headers 2>/dev/null | wc -l || true)
if [[ $BACKUP_SCHEDULE -gt 0 ]]; then
    SCHEDULE_JSON=$(oc --context="$NEW_HUB_CONTEXT" get $RES_BACKUP_SCHEDULE -n "$BACKUP_NAMESPACE" -o json 2>/dev/null | jq '.items[0]' 2>/dev/null || echo "")
    SCHEDULE_NAME=$(echo "$SCHEDULE_JSON" | jq -r '.metadata.name // ""' 2>/dev/null || echo "")
    PAUSED=$(echo "$SCHEDULE_JSON" | jq -r '.spec.paused // false' 2>/dev/null || echo "")

    # Detect GitOps markers on BackupSchedule
    if [[ -n "$SCHEDULE_JSON" ]]; then
        GITOPS_MARKERS=$(detect_gitops_markers "$SCHEDULE_JSON")
        if [[ -n "$GITOPS_MARKERS" ]]; then
            collect_gitops_markers "new-hub" "$BACKUP_NAMESPACE" "BackupSchedule" "$SCHEDULE_NAME" "$GITOPS_MARKERS"
        fi
    fi

    if [[ "$PAUSED" == "false" ]] || [[ -z "$PAUSED" ]]; then
        check_pass "BackupSchedule '$SCHEDULE_NAME' is enabled (not paused)"
        
        # Check for BackupCollision state (indicates scheduling conflict)
        COLLISION_STATUS=$(oc --context="$NEW_HUB_CONTEXT" get $RES_BACKUP_SCHEDULE "$SCHEDULE_NAME" -n "$BACKUP_NAMESPACE" -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
        if [[ "$COLLISION_STATUS" == "BackupCollision" ]]; then
            check_fail "BackupSchedule in BackupCollision state (needs recreation)"
            echo -e "${RED}       The BackupSchedule was likely restored from primary hub and conflicts with existing backups${NC}"
            echo -e "${RED}       Resolution: Delete and recreate the BackupSchedule resource${NC}"
        elif [[ -n "$COLLISION_STATUS" ]]; then
            check_pass "BackupSchedule status: $COLLISION_STATUS"
        fi
    else
        check_fail "BackupSchedule '$SCHEDULE_NAME' is paused (should be enabled on new hub)"
    fi
    
    # Derive an effective backup age threshold from the schedule cadence
    EFFECTIVE_BACKUP_AGE_MAX_SECONDS="$BACKUP_AGE_MAX_SECONDS"
    SCHEDULE_EXPR=$(oc --context="$NEW_HUB_CONTEXT" get $RES_BACKUP_SCHEDULE "$SCHEDULE_NAME" -n "$BACKUP_NAMESPACE" -o jsonpath='{.spec.veleroSchedule}' 2>/dev/null || echo "")
    INTERVAL_SECONDS=""
    if [[ -n "$SCHEDULE_EXPR" ]]; then
        INTERVAL_SECONDS=$(_derive_backup_interval_seconds "$SCHEDULE_EXPR")
        if [[ -n "$INTERVAL_SECONDS" ]] && [[ "$INTERVAL_SECONDS" -gt "$BACKUP_AGE_MAX_SECONDS" ]]; then
            # Match Python behavior: threshold = schedule interval + default max age
            EFFECTIVE_BACKUP_AGE_MAX_SECONDS=$((INTERVAL_SECONDS + BACKUP_AGE_MAX_SECONDS))
        fi
    fi
    
    # Check for recent backups
    RECENT_BACKUPS=$(oc --context="$NEW_HUB_CONTEXT" get $RES_BACKUP -n "$BACKUP_NAMESPACE" --sort-by=.metadata.creationTimestamp --no-headers 2>/dev/null | tail -3 | wc -l || true)
    if [[ $RECENT_BACKUPS -gt 0 ]]; then
        # Get details of the latest backup in a single call
        read -r LATEST_BACKUP LATEST_PHASE LATEST_TIME <<< "$(oc --context="$NEW_HUB_CONTEXT" get $RES_BACKUP -n "$BACKUP_NAMESPACE" --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name} {.items[-1].status.phase} {.items[-1].metadata.creationTimestamp}' 2>/dev/null)"
        
        if [[ "$LATEST_PHASE" == "Completed" ]] || [[ "$LATEST_PHASE" == "Finished" ]]; then
            check_pass "Latest backup: '$LATEST_BACKUP' (Phase: $LATEST_PHASE, Created: $LATEST_TIME)"
        elif [[ "$LATEST_PHASE" == "InProgress" ]]; then
            check_warn "Latest backup: '$LATEST_BACKUP' is InProgress (Created: $LATEST_TIME)"
        else
            check_fail "Latest backup: '$LATEST_BACKUP' failed or in unexpected state: $LATEST_PHASE"
        fi

        # Check backup age relative to schedule frequency
        if [[ -n "$LATEST_TIME" ]]; then
            LATEST_EPOCH=$(date -d "$LATEST_TIME" +%s 2>/dev/null || echo 0)
            CURRENT_EPOCH=$(date +%s)
            if [[ $LATEST_EPOCH -gt 0 ]]; then
                AGE_SECONDS=$((CURRENT_EPOCH - LATEST_EPOCH))
                AGE_DISPLAY=$(format_age_display "$AGE_SECONDS")
                if [[ -n "$INTERVAL_SECONDS" ]]; then
                    INTERVAL_DISPLAY=$(format_age_display "$INTERVAL_SECONDS")
                    THRESHOLD_DISPLAY=$(format_age_display "$EFFECTIVE_BACKUP_AGE_MAX_SECONDS")
                fi

                if [[ $AGE_SECONDS -gt $EFFECTIVE_BACKUP_AGE_MAX_SECONDS ]]; then
                    if [[ "$LATEST_PHASE" == "InProgress" ]]; then
                        check_warn "Backup '$LATEST_BACKUP' running longer than expected: $AGE_DISPLAY"
                    elif [[ -n "$INTERVAL_SECONDS" ]]; then
                        check_fail "Latest backup is too old: $AGE_DISPLAY (schedule: $SCHEDULE_EXPR, interval: $INTERVAL_DISPLAY, threshold: $THRESHOLD_DISPLAY)"
                    else
                        check_fail "Latest backup is older than $((EFFECTIVE_BACKUP_AGE_MAX_SECONDS / 60)) minutes ($AGE_DISPLAY ago)"
                    fi
                else
                    if [[ -n "$INTERVAL_SECONDS" ]]; then
                        check_pass "Latest backup age OK: $AGE_DISPLAY (schedule: $SCHEDULE_EXPR, interval: $INTERVAL_DISPLAY, threshold: $THRESHOLD_DISPLAY)"
                    else
                        check_pass "Latest backup age OK ($AGE_DISPLAY)"
                    fi
                fi
            else
                check_warn "Could not parse latest backup timestamp for age validation"
            fi
        fi

        # Check Velero logs for backup errors
        VELERO_LOG_OUTPUT=$(oc --context="$NEW_HUB_CONTEXT" logs -n "$BACKUP_NAMESPACE" "$VELERO_DEPLOYMENT" -c velero --tail=500 2>/dev/null || true)
        if [[ -n "$VELERO_LOG_OUTPUT" ]]; then
            VELERO_LOG_ERRORS=$(echo "$VELERO_LOG_OUTPUT" | grep "$LATEST_BACKUP" | grep -iE "error|failed" | head -1 || true)
            if [[ -n "$VELERO_LOG_ERRORS" ]]; then
                check_fail "Velero logs show errors for backup '$LATEST_BACKUP' (see velero logs)"
            else
                check_pass "Velero logs show no errors for backup '$LATEST_BACKUP' (recent logs checked)"
            fi
        else
            check_warn "Velero logs unavailable for backup verification (check manually)"
        fi
    else
        check_warn "No recent backups found (may take time for first backup to run)"
    fi
else
    check_fail "No BackupSchedule found on new hub"
fi

# Check 5b: Verify BackupStorageLocation is available
BSL_OUTPUT=$(oc --context="$NEW_HUB_CONTEXT" get $RES_BSL -n "$BACKUP_NAMESPACE" --no-headers 2>/dev/null || true)
if [[ -n "$BSL_OUTPUT" ]]; then
    BSL_NAME=$(echo "$BSL_OUTPUT" | awk '{print $1}' | head -1)
    BSL_PHASE=$(oc --context="$NEW_HUB_CONTEXT" get $RES_BSL "$BSL_NAME" -n "$BACKUP_NAMESPACE" -o jsonpath='{.status.phase}' 2>/dev/null || echo "unknown")
    
    if [[ "$BSL_PHASE" == "Available" ]]; then
        check_pass "BackupStorageLocation '$BSL_NAME' is Available (storage accessible)"
    else
        check_fail "BackupStorageLocation '$BSL_NAME' is in '$BSL_PHASE' state (should be Available)"
        echo -e "${RED}       Unavailable BSL means restores cannot proceed${NC}"
        echo -e "${RED}       Backup storage may be inaccessible - verify credentials and connectivity${NC}"
        BSL_CONDITIONS=$(oc --context="$NEW_HUB_CONTEXT" get $RES_BSL "$BSL_NAME" -n "$BACKUP_NAMESPACE" -o json 2>/dev/null | \
            jq -r '.status.conditions // [] | map("\(.type)=\(.status) reason=\(.reason // "n/a") msg=\(.message // "n/a")") | join("; ")' || true)
        if [[ -n "$BSL_CONDITIONS" ]]; then
            echo -e "${RED}       BSL conditions: $BSL_CONDITIONS${NC}"
        else
            echo -e "${YELLOW}       BSL conditions: none reported${NC}"
        fi
    fi
else
    check_warn "No BackupStorageLocation found (OADP may not be configured)"
fi

# Check 6: Verify ACM hub components
section_header "6. Checking ACM Hub Components"

MCH_COUNT=$(oc --context="$NEW_HUB_CONTEXT" get $RES_MCH -n "$ACM_NAMESPACE" --no-headers 2>/dev/null | wc -l || true)
if [[ $MCH_COUNT -eq 1 ]]; then
    MCH_NAME=$(oc --context="$NEW_HUB_CONTEXT" get $RES_MCH -n "$ACM_NAMESPACE" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    MCH_PHASE=$(oc --context="$NEW_HUB_CONTEXT" get $RES_MCH "$MCH_NAME" -n "$ACM_NAMESPACE" -o jsonpath='{.status.phase}' 2>/dev/null)
    
    if [[ "$MCH_PHASE" == "Running" ]]; then
        check_pass "MultiClusterHub '$MCH_NAME' is Running"
    else
        check_fail "MultiClusterHub '$MCH_NAME' in unexpected phase: $MCH_PHASE"
    fi
else
    check_fail "Expected 1 MultiClusterHub, found $MCH_COUNT"
fi

# Check ACM pods
ACM_PODS_RUNNING=$(oc --context="$NEW_HUB_CONTEXT" get pods -n "$ACM_NAMESPACE" --no-headers 2>/dev/null | grep -c "Running" || true)
ACM_PODS_TOTAL=$(oc --context="$NEW_HUB_CONTEXT" get pods -n "$ACM_NAMESPACE" --no-headers 2>/dev/null | wc -l || true)

if [[ $ACM_PODS_RUNNING -eq $ACM_PODS_TOTAL ]] && [[ $ACM_PODS_TOTAL -gt 0 ]]; then
    check_pass "All $ACM_PODS_TOTAL ACM pods are Running"
else
    check_warn "$ACM_PODS_RUNNING of $ACM_PODS_TOTAL ACM pods are Running"
fi

# Check 7: Compare with old hub (if provided)
if [[ -n "$OLD_HUB_CONTEXT" ]]; then
    section_header "7. Comparing with Old Hub"
    
    # Check old hub cluster status
    OLD_CLUSTERS=$(oc --context="$OLD_HUB_CONTEXT" get $RES_MANAGED_CLUSTER --no-headers 2>/dev/null | grep -c -v "$LOCAL_CLUSTER_NAME" || true)
    if [[ $OLD_CLUSTERS -gt 0 ]]; then
        OLD_UNKNOWN=$(oc --context="$OLD_HUB_CONTEXT" get $RES_MANAGED_CLUSTER -o json 2>/dev/null | \
            jq -r --arg LOCAL "$LOCAL_CLUSTER_NAME" '.items[] | select(.metadata.name != $LOCAL) | select(.status.conditions[]? | select(.type=="ManagedClusterConditionAvailable" and .status!="True")) | .metadata.name' | wc -l)
        
        if [[ $OLD_UNKNOWN -eq $OLD_CLUSTERS ]]; then
            check_pass "All $OLD_CLUSTERS cluster(s) on old hub show as Unknown/Unavailable (expected)"
        else
            check_warn "Some clusters on old hub still show as Available (may need time to disconnect)"
        fi
    fi
    
    # Check if old hub BackupSchedule is paused
    OLD_SCHEDULE=$(oc --context="$OLD_HUB_CONTEXT" get $RES_BACKUP_SCHEDULE -n "$BACKUP_NAMESPACE" --no-headers 2>/dev/null | wc -l || true)
    if [[ $OLD_SCHEDULE -gt 0 ]]; then
        OLD_SCHEDULE_NAME=$(oc --context="$OLD_HUB_CONTEXT" get $RES_BACKUP_SCHEDULE -n "$BACKUP_NAMESPACE" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
        OLD_PAUSED=$(oc --context="$OLD_HUB_CONTEXT" get $RES_BACKUP_SCHEDULE "$OLD_SCHEDULE_NAME" -n "$BACKUP_NAMESPACE" -o jsonpath='{.spec.paused}' 2>/dev/null)
        
        if [[ "$OLD_PAUSED" == "true" ]]; then
            check_pass "Old hub BackupSchedule is paused (expected)"
        else
            check_warn "Old hub BackupSchedule is not paused (should be paused after switchover)"
        fi
    fi
    
    # Old hub observability safety:
    # The previous primary must either have no MCO, or (if MCO exists) have key components scaled down.
    if oc --context="$OLD_HUB_CONTEXT" get namespace "$OBSERVABILITY_NAMESPACE" &> /dev/null; then
        if oc --context="$OLD_HUB_CONTEXT" get $RES_MCO observability &> /dev/null; then
            # Detect GitOps markers on old hub MCO (cluster-scoped, no namespace)
            OLD_MCO_JSON=$(oc --context="$OLD_HUB_CONTEXT" get $RES_MCO observability -o json 2>/dev/null || echo "{}")
            if [[ -n "$OLD_MCO_JSON" && "$OLD_MCO_JSON" != "{}" ]]; then
                OLD_MCO_NAME=$(echo "$OLD_MCO_JSON" | jq -r '.metadata.name // "observability"')
                GITOPS_MARKERS=$(detect_gitops_markers "$OLD_MCO_JSON")
                if [[ -n "$GITOPS_MARKERS" ]]; then
                    collect_gitops_markers "old-hub" "" "MultiClusterObservability" "$OLD_MCO_NAME" "$GITOPS_MARKERS"
                fi
            fi

            OLD_COMPACTOR=$(get_pod_count "$OLD_HUB_CONTEXT" "$OBSERVABILITY_NAMESPACE" "app.kubernetes.io/name=thanos-compact" "$OBS_THANOS_COMPACT_POD")
            OLD_OBSERVATORIUM_API_PODS=$(get_pod_count "$OLD_HUB_CONTEXT" "$OBSERVABILITY_NAMESPACE" "app.kubernetes.io/name=observatorium-api" "$OBS_API_POD")

            if [[ $OLD_COMPACTOR -eq 0 ]] && [[ $OLD_OBSERVATORIUM_API_PODS -eq 0 ]]; then
                check_pass "Old hub: MultiClusterObservability present but Thanos compactor and observatorium-api are scaled to 0 (expected)"
            else
                check_fail "Old hub: MultiClusterObservability is still active (thanos-compact=$OLD_COMPACTOR, observatorium-api=$OLD_OBSERVATORIUM_API_PODS). Scale both to 0 or remove MCO."
            fi
        else
            check_pass "Old hub: MultiClusterObservability CR not present (expected)"
        fi

        check_warn "Do NOT re-enable Thanos compactor or Observatorium API on the old hub unless switching back"
    fi
    
    # Check if old hub has passive sync restore configured (for failback capability)
    OLD_RESTORE=$(oc --context="$OLD_HUB_CONTEXT" get $RES_RESTORE -n "$BACKUP_NAMESPACE" --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name}' 2>/dev/null || true)
    if [[ -n "$OLD_RESTORE" ]]; then
        OLD_RESTORE_PHASE=$(oc --context="$OLD_HUB_CONTEXT" get $RES_RESTORE "$OLD_RESTORE" -n "$BACKUP_NAMESPACE" -o jsonpath='{.status.phase}' 2>/dev/null || echo "Unknown")
        OLD_RESTORE_SYNC=$(oc --context="$OLD_HUB_CONTEXT" get $RES_RESTORE "$OLD_RESTORE" -n "$BACKUP_NAMESPACE" -o jsonpath='{.spec.syncRestoreWithNewBackups}' 2>/dev/null || echo "false")
        
        if [[ "$OLD_RESTORE_SYNC" == "true" ]] && [[ "$OLD_RESTORE_PHASE" == "Enabled" || "$OLD_RESTORE_PHASE" == "Finished" ]]; then
            check_pass "Old hub has passive sync restore '$OLD_RESTORE' (Phase: $OLD_RESTORE_PHASE) - ready for failback"
        elif [[ "$OLD_RESTORE_SYNC" == "true" ]]; then
            check_warn "Old hub has passive sync restore '$OLD_RESTORE' but phase is: $OLD_RESTORE_PHASE"
        else
            check_warn "Old hub restore '$OLD_RESTORE' is not configured for passive sync (failback not available)"
        fi
    else
        check_warn "Old hub has no restore configured (consider setting up passive sync for failback capability)"
    fi
    
    # Check if old hub ACM is still installed (for decommission status)
    OLD_MCH=$(oc --context="$OLD_HUB_CONTEXT" get $RES_MCH -n "$ACM_NAMESPACE" --no-headers 2>/dev/null | wc -l || true)
    if [[ $OLD_MCH -gt 0 ]]; then
        check_pass "Old hub: ACM still installed (expected if keeping as secondary)"
    else
        check_pass "Old hub: ACM has been decommissioned"
    fi
fi

# Check 8: Verify no auto-import disabled annotations
section_header "8. Checking Auto-Import Status"

DISABLED_AUTO_IMPORT=$(oc --context="$NEW_HUB_CONTEXT" get $RES_MANAGED_CLUSTER -o json 2>/dev/null | \
    jq -r --arg LOCAL "$LOCAL_CLUSTER_NAME" '.items[] | select(.metadata.name != $LOCAL) | select(.metadata.annotations["import.open-cluster-management.io/disable-auto-import"] != null) | .metadata.name' 2>/dev/null | wc -l || true)

if [[ $DISABLED_AUTO_IMPORT -eq 0 ]]; then
    check_pass "No clusters have disable-auto-import annotation on new hub (expected)"
else
    check_warn "$DISABLED_AUTO_IMPORT cluster(s) have disable-auto-import annotation (may be intentional)"
fi

# Check 9: Verify Auto-Import Strategy (ACM 2.14+)
section_header "9. Checking Auto-Import Strategy (ACM 2.14+)"

# Get ACM version on new hub and detect GitOps markers on MCH
NEW_HUB_MCH_JSON=$(oc --context="$NEW_HUB_CONTEXT" get $RES_MCH -n "$ACM_NAMESPACE" -o json 2>/dev/null | jq '.items[0]' 2>/dev/null || echo "{}")
if [[ -n "$NEW_HUB_MCH_JSON" && "$NEW_HUB_MCH_JSON" != "{}" ]]; then
    NEW_HUB_MCH_NAME=$(echo "$NEW_HUB_MCH_JSON" | jq -r '.metadata.name // "multiclusterhub"')
    GITOPS_MARKERS=$(detect_gitops_markers "$NEW_HUB_MCH_JSON")
    if [[ -n "$GITOPS_MARKERS" ]]; then
        collect_gitops_markers "new-hub" "$ACM_NAMESPACE" "MultiClusterHub" "$NEW_HUB_MCH_NAME" "$GITOPS_MARKERS"
    fi
    NEW_HUB_VERSION=$(echo "$NEW_HUB_MCH_JSON" | jq -r '.status.currentVersion // "unknown"')
else
    NEW_HUB_VERSION=$(oc --context="$NEW_HUB_CONTEXT" get $RES_MCH -n "$ACM_NAMESPACE" -o jsonpath='{.items[0].status.currentVersion}' 2>/dev/null || echo "unknown")
fi

if [[ "$NEW_HUB_VERSION" == "unknown" ]]; then
    check_fail "New hub: Could not determine ACM version. Skipping auto-import strategy check."
fi

# Check new hub auto-import strategy
NEW_HUB_STRATEGY=$(get_auto_import_strategy "$NEW_HUB_CONTEXT")

if [[ "$NEW_HUB_VERSION" != "unknown" ]] && is_acm_214_or_higher "$NEW_HUB_VERSION"; then
    if [[ "$NEW_HUB_STRATEGY" == "error" ]]; then
        check_fail "New hub: Could not retrieve autoImportStrategy (connection or API error)"
    elif [[ "$NEW_HUB_STRATEGY" == "default" ]]; then
        check_pass "New hub: Using default autoImportStrategy ($AUTO_IMPORT_STRATEGY_DEFAULT) - correct post-switchover state"
    elif [[ "$NEW_HUB_STRATEGY" == "$AUTO_IMPORT_STRATEGY_DEFAULT" ]]; then
        check_pass "New hub: autoImportStrategy explicitly set to $AUTO_IMPORT_STRATEGY_DEFAULT"
    else
        check_warn "New hub: autoImportStrategy is set to '$NEW_HUB_STRATEGY' (non-default)"
        echo -e "${YELLOW}       Post-switchover, this should be removed to restore default behavior.${NC}"
        echo -e "${YELLOW}       To reset to default, run:${NC}"
        echo -e "${YELLOW}         oc -n $MCE_NAMESPACE delete configmap $IMPORT_CONTROLLER_CONFIGMAP${NC}"
        echo -e "${YELLOW}       See: $AUTO_IMPORT_STRATEGY_DOC_URL${NC}"
    fi
elif [[ "$NEW_HUB_VERSION" != "unknown" ]]; then
    check_pass "New hub: ACM version $NEW_HUB_VERSION (autoImportStrategy check not applicable for versions < 2.14)"
fi

# Also check old hub if provided
if [[ -n "$OLD_HUB_CONTEXT" ]]; then
    if ! OLD_HUB_VERSION=$(oc --context="$OLD_HUB_CONTEXT" get $RES_MCH -n "$ACM_NAMESPACE" -o jsonpath='{.items[0].status.currentVersion}' 2>/dev/null) || [[ -z "$OLD_HUB_VERSION" ]]; then
        check_warn "Old hub: Could not determine ACM version. Skipping auto-import strategy check."
        OLD_HUB_VERSION="unknown"
    fi
    OLD_HUB_STRATEGY=$(get_auto_import_strategy "$OLD_HUB_CONTEXT")
    
    if [[ "$OLD_HUB_VERSION" != "unknown" ]] && is_acm_214_or_higher "$OLD_HUB_VERSION"; then
        if [[ "$OLD_HUB_STRATEGY" == "error" ]]; then
            check_warn "Old hub: Could not retrieve autoImportStrategy (connection or API error)"
        elif [[ "$OLD_HUB_STRATEGY" == "default" ]] || [[ "$OLD_HUB_STRATEGY" == "$AUTO_IMPORT_STRATEGY_DEFAULT" ]]; then
            check_pass "Old hub: autoImportStrategy is default/ImportOnly"
        else
            check_warn "Old hub: autoImportStrategy is set to '$OLD_HUB_STRATEGY'"
            echo -e "${YELLOW}       This should be reset if no longer needed.${NC}"
            echo -e "${YELLOW}       See: $AUTO_IMPORT_STRATEGY_DOC_URL${NC}"
        fi
    fi
fi

if [[ $ARGOCD_CHECK -eq 1 ]]; then
    section_header "10. Checking ArgoCD GitOps Management (Optional)"
    check_argocd_acm_resources "$NEW_HUB_CONTEXT" "New hub"
    if [[ -n "$OLD_HUB_CONTEXT" ]]; then
        check_argocd_acm_resources "$OLD_HUB_CONTEXT" "Old hub"
    fi
fi

# Print GitOps detection report if any markers were found
print_gitops_report

# Summary and exit
if print_summary "postflight"; then
    exit "$EXIT_SUCCESS"
else
    exit "$EXIT_FAILURE"
fi
