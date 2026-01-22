#!/bin/bash
#
# Common Library for ACM Switchover Scripts
#
# This file contains shared functions and variables used by preflight-check.sh
# and postflight-check.sh. Sourcing this file ensures consistency and reduces
# code duplication across scripts.
#
# Usage:
#   source "${SCRIPT_DIR}/lib-common.sh"

# Prevent multiple sourcing
if [[ -n "${_LIB_COMMON_LOADED:-}" ]]; then
    return 0
fi
_LIB_COMMON_LOADED=1

# =============================================================================
# Colors for output
# =============================================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
GRAY='\033[0;90m'
NC='\033[0m' # No Color

# =============================================================================
# Counters
# =============================================================================
TOTAL_CHECKS=0
PASSED_CHECKS=0
FAILED_CHECKS=0
WARNING_CHECKS=0

# =============================================================================
# Arrays to store messages
# =============================================================================
FAILED_MESSAGES=()
WARNING_MESSAGES=()

# =============================================================================
# Version Display
# =============================================================================

# Print script version and name
# Usage: print_script_version "script-name"
# Example: print_script_version "preflight-check"
print_script_version() {
    local script_name="${1:-script}"
    echo -e "${GRAY}${script_name} v${SCRIPT_VERSION} (${SCRIPT_VERSION_DATE})${NC}"
}

# Print version info in a banner format (for script headers)
# Usage: print_version_banner "Script Title"
print_version_banner() {
    local title="$1"
    echo ""
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║   $title"
    printf "║   %-57s ║\n" "Version: ${SCRIPT_VERSION} (${SCRIPT_VERSION_DATE})"
    echo "╚════════════════════════════════════════════════════════════╝"
}

# =============================================================================
# Helper Functions
# =============================================================================

# Record a passing check
# Usage: check_pass "Check description"
check_pass() {
    ((TOTAL_CHECKS++)) || true
    ((PASSED_CHECKS++)) || true
    echo -e "${GREEN}✓${NC} $1"
}

# Record a failing check
# Usage: check_fail "Check description"
check_fail() {
    ((TOTAL_CHECKS++)) || true
    ((FAILED_CHECKS++)) || true
    FAILED_MESSAGES+=("$1")
    echo -e "${RED}✗${NC} $1"
}

# Record a warning
# Usage: check_warn "Check description"
check_warn() {
    ((TOTAL_CHECKS++)) || true
    ((WARNING_CHECKS++)) || true
    WARNING_MESSAGES+=("$1")
    echo -e "${YELLOW}⚠${NC} $1"
}

# Print a section header
# Usage: section_header "Section Title"
section_header() {
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

# =============================================================================
# CLI Detection
# =============================================================================

# Detect and configure the cluster CLI (oc or kubectl) and jq
# Sets: CLUSTER_CLI_BIN, CLUSTER_CLI_NAME
# Defines: oc() function alias if using kubectl
# Usage: detect_cluster_cli
detect_cluster_cli() {
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
        # Note: Using "$@" is safe against shell injection because it correctly
        # preserves each argument as a separate string, preventing the shell from
        # interpreting metacharacters within them.
        oc() {
            kubectl "$@"
        }
        check_pass "$CLUSTER_CLI_NAME is installed"
    else
        check_fail "Neither oc nor kubectl CLI found"
    fi

    # Check for jq (required for JSON processing)
    if command -v jq &> /dev/null; then
        check_pass "jq is installed"
    else
        check_fail "jq not found (required for JSON processing)"
    fi

    # Print CLI info if available
    if [[ -n "$CLUSTER_CLI_BIN" ]]; then
        echo "Using CLI: $CLUSTER_CLI_NAME ($(command -v "$CLUSTER_CLI_BIN"))"
    fi
}

# =============================================================================
# Auto-Import Strategy Helpers (ACM 2.14+)
# =============================================================================

# Get the autoImportStrategy value from a hub
# Returns: "ImportOnly", "ImportAndSync", "default" (if not configured), or "error"
# Usage: get_auto_import_strategy "$CONTEXT"
get_auto_import_strategy() {
    local context="$1"
    local output
    local exit_code
    
    # Attempt to get the configmap, capturing stdout and stderr together
    output=$(oc --context="$context" get configmap "$IMPORT_CONTROLLER_CONFIGMAP" -n "$MCE_NAMESPACE" \
        -o jsonpath="{.data.${AUTO_IMPORT_STRATEGY_KEY}}" 2>&1)
    exit_code=$?
    
    if [[ $exit_code -ne 0 ]]; then
        if [[ "$output" == *"NotFound"* ]]; then
            # ConfigMap doesn't exist, which is a valid "default" state
            echo "default"
            return 0
        else
            # A different error occurred (e.g., connection refused)
            echo "error"
            echo "$output" >&2
            # Avoid aborting callers that use set -e and command substitution
            return 0
        fi
    fi
    
    if [[ -z "$output" ]]; then
        # ConfigMap exists but the key is missing or empty
        echo "default"
    else
        echo "$output"
    fi

    return 0
}

# Check if ACM version is 2.14 or higher
# Usage: is_acm_214_or_higher "$VERSION"
# Returns 0 (true) if version >= 2.14, 1 (false) otherwise
is_acm_214_or_higher() {
    local version="$1"
    
    # Extract major and minor version (e.g., "2.14.0" -> major=2, minor=14)
    local major minor
    major=$(echo "$version" | cut -d'.' -f1)
    minor=$(echo "$version" | cut -d'.' -f2)
    
    # Handle unknown versions
    if [[ -z "$major" ]] || [[ -z "$minor" ]]; then
        return 1
    fi

    # Validate that major and minor are numeric
    if ! [[ "$major" =~ ^[0-9]+$ ]] || ! [[ "$minor" =~ ^[0-9]+$ ]]; then
        return 1
    fi
    
    # Check if version is 2.14 or higher
    if [[ "$major" -gt 2 ]] || { [[ "$major" -eq 2 ]] && [[ "$minor" -ge 14 ]]; }; then
        return 0
    else
        return 1
    fi
}

# =============================================================================
# Managed Cluster Helpers
# =============================================================================

# Get total managed cluster count (excluding local-cluster)
# Usage: get_total_mc_count "$CONTEXT"
# Note: Returns 0 if ManagedCluster CRD doesn't exist or permissions are lacking
get_total_mc_count() {
    local ctx="$1"
    local count
    
    count=$("$CLUSTER_CLI_BIN" --context="$ctx" get $RES_MANAGED_CLUSTER --no-headers 2>/dev/null | \
        grep -v "$LOCAL_CLUSTER_NAME" | wc -l || echo "0")
    
    # Trim whitespace and ensure numeric
    count=$(echo "$count" | tr -d '[:space:]')
    echo "${count:-0}"
}

# Get count of available (connected) managed clusters (excluding local-cluster)
# Usage: get_available_mc_count "$CONTEXT"
# Note: Returns 0 if ManagedCluster CRD doesn't exist or permissions are lacking
get_available_mc_count() {
    local ctx="$1"
    local count
    
    count=$("$CLUSTER_CLI_BIN" --context="$ctx" get $RES_MANAGED_CLUSTER -o json 2>/dev/null | \
        jq -r --arg LOCAL "$LOCAL_CLUSTER_NAME" \
        '[.items[] | select(.metadata.name != $LOCAL) | select(.status.conditions[]? | select(.type=="ManagedClusterConditionAvailable" and .status=="True"))] | length' \
        2>/dev/null || echo "0")
    
    # Trim whitespace and ensure numeric
    count=$(echo "$count" | tr -d '[:space:]')
    echo "${count:-0}"
}

# Get BackupSchedule state
# Returns: "active", "paused", "collision", "none", or "error"
get_backup_schedule_state() {
    local ctx="$1"
    
    local schedule_name
    schedule_name=$("$CLUSTER_CLI_BIN" --context="$ctx" get $RES_BACKUP_SCHEDULE -n "$BACKUP_NAMESPACE" \
        -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
    
    if [[ -z "$schedule_name" ]]; then
        echo "none"
        return
    fi
    
    local paused
    paused=$("$CLUSTER_CLI_BIN" --context="$ctx" get $RES_BACKUP_SCHEDULE "$schedule_name" -n "$BACKUP_NAMESPACE" \
        -o jsonpath='{.spec.paused}' 2>/dev/null || echo "")
    
    local phase
    phase=$("$CLUSTER_CLI_BIN" --context="$ctx" get $RES_BACKUP_SCHEDULE "$schedule_name" -n "$BACKUP_NAMESPACE" \
        -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
    
    if [[ "$phase" == "BackupCollision" ]]; then
        echo "collision"
    elif [[ "$paused" == "true" ]]; then
        echo "paused"
    elif [[ "$phase" == "Enabled" ]]; then
        echo "active"
    else
        # Return the phase directly for other states
        echo "${phase:-error}"
    fi
}

# Print a hub summary card (similar to discover-hub.sh output)
# Usage: print_hub_summary "$CONTEXT" "$VERSION" "$ROLE" "$AVAILABLE_MC" "$TOTAL_MC" "$STATE_DESC"
print_hub_summary() {
    local ctx="$1"
    local version="$2"
    local role="$3"
    local available_mc="$4"
    local total_mc="$5"
    local state_desc="$6"
    
    # Color based on role
    local role_color="$NC"
    case "$role" in
        primary)
            role_color="$GREEN"
            ;;
        secondary)
            role_color="$BLUE"
            ;;
        standby|old-primary)
            role_color="$YELLOW"
            ;;
        *)
            role_color="$NC"
            ;;
    esac
    
    # Color for cluster counts
    local cluster_color="$GREEN"
    if [[ "$available_mc" -lt "$total_mc" ]]; then
        cluster_color="$YELLOW"
    fi
    if [[ "$available_mc" -eq 0 ]] && [[ "$total_mc" -gt 0 ]]; then
        cluster_color="$RED"
    fi
    
    echo ""
    echo -e "  ${role_color}●${NC} ${BLUE}$ctx${NC}"
    echo -e "    Role:     ${role_color}$role${NC}"
    echo -e "    Version:  $version"
    echo -e "    Clusters: ${cluster_color}${available_mc}/${total_mc}${NC} (available/total)"
    if [[ -n "$state_desc" ]]; then
        echo -e "    State:    $state_desc"
    fi
}

# =============================================================================
# Observability Helpers
# =============================================================================

# Get count of running pods matching a label or name prefix
# Usage: get_running_pod_count "$CONTEXT" "$NAMESPACE" "$LABEL" "$NAME_PREFIX"
# Returns the count of running pods (0 if none found)
get_running_pod_count() {
    local ctx="$1"
    local namespace="$2"
    local label="$3"
    local name_prefix="$4"
    local count=0

    # Try by label first
    if [[ -n "$label" ]]; then
        count=$("$CLUSTER_CLI_BIN" --context="$ctx" get pods -n "$namespace" -l "$label" --no-headers 2>/dev/null | grep -c "Running" || true)
    fi

    # Fallback to name prefix if label check returns 0
    if [[ $count -eq 0 ]] && [[ -n "$name_prefix" ]]; then
        count=$("$CLUSTER_CLI_BIN" --context="$ctx" get pods -n "$namespace" --no-headers 2>/dev/null | grep "^${name_prefix}" | grep -c "Running" || true)
    fi

    echo "${count:-0}"
}

# Get count of all pods (any state) matching a label or name prefix
# Usage: get_pod_count "$CONTEXT" "$NAMESPACE" "$LABEL" "$NAME_PREFIX"
# Returns the count of all pods regardless of state (0 if none found)
# Use this for scale-down validation where any pod (Running, Pending, etc) indicates not scaled to 0
get_pod_count() {
    local ctx="$1"
    local namespace="$2"
    local label="$3"
    local name_prefix="$4"
    local count=0

    # Try by label first
    if [[ -n "$label" ]]; then
        count=$("$CLUSTER_CLI_BIN" --context="$ctx" get pods -n "$namespace" -l "$label" --no-headers 2>/dev/null | wc -l || true)
    fi

    # Fallback to name prefix if label check returns 0
    if [[ $count -eq 0 ]] && [[ -n "$name_prefix" ]]; then
        count=$("$CLUSTER_CLI_BIN" --context="$ctx" get pods -n "$namespace" --no-headers 2>/dev/null | grep -c "^${name_prefix}" || true)
    fi

    echo "${count:-0}"
}

# =============================================================================
# Cluster Health Helpers
# =============================================================================

# Check ClusterOperators health on a given context
# Usage: check_cluster_operators "$CONTEXT" "Hub name"
# Returns 0 on success, sets check_pass/check_fail/check_warn as appropriate
check_cluster_operators() {
    local ctx="$1"
    local hub_name="$2"
    local co_json

    co_json=$("$CLUSTER_CLI_BIN" --context="$ctx" get clusteroperators -o json 2>/dev/null || true)
    if [[ -z "$co_json" ]]; then
        check_pass "$hub_name: ClusterOperators not available (non-OpenShift cluster or insufficient permissions)"
        return 0
    fi

    local co_output
    co_output=$(echo "$co_json" | jq -r '.items[] | .metadata.name' 2>/dev/null || true)
    if [[ -z "$co_output" ]]; then
        check_pass "$hub_name: ClusterOperators not available (non-OpenShift cluster or insufficient permissions)"
        return 0
    fi

    local co_total co_degraded co_unavailable
    co_total=$(echo "$co_output" | wc -l)
    co_degraded=$(echo "$co_json" | jq -r '.items[] | select(.status.conditions[]? | select(.type=="Degraded" and .status=="True")) | .metadata.name' 2>/dev/null | wc -l || true)
    co_unavailable=$(echo "$co_json" | jq -r '.items[] | select(.status.conditions[]? | select(.type=="Available" and .status=="False")) | .metadata.name' 2>/dev/null | wc -l || true)

    if [[ $co_degraded -eq 0 ]] && [[ $co_unavailable -eq 0 ]]; then
        check_pass "$hub_name: All $co_total ClusterOperator(s) are healthy"
    else
        local unhealthy=$((co_degraded + co_unavailable))
        check_fail "$hub_name: $unhealthy ClusterOperator(s) are degraded or unavailable"
        local degraded_list
        degraded_list=$(echo "$co_json" | jq -r '.items[] | select(.status.conditions[]? | select(.type=="Degraded" and .status=="True")) | .metadata.name' 2>/dev/null || true)
        if [[ -n "$degraded_list" ]]; then
            echo -e "${RED}       Degraded operators: $(echo "$degraded_list" | tr '\n' ' ')${NC}"
        fi
    fi

    return 0
}

# Check ClusterVersion upgrade status on a given context
# Usage: check_cluster_upgrade_status "$CONTEXT" "Hub name"
check_cluster_upgrade_status() {
    local ctx="$1"
    local hub_name="$2"
    local cv_output

    cv_output=$("$CLUSTER_CLI_BIN" --context="$ctx" get clusterversion version -o json 2>/dev/null || true)
    if [[ -z "$cv_output" ]]; then
        check_pass "$hub_name: ClusterVersion not available (non-OpenShift cluster or insufficient permissions)"
        return 0
    fi

    local upgrading ocp_version
    upgrading=$(echo "$cv_output" | jq -r '.status.conditions[]? | select(.type=="Progressing" and .status=="True") | .message' || true)
    ocp_version=$(echo "$cv_output" | jq -r '.status.desired.version // "unknown"' || true)

    if [[ -n "$upgrading" && "$upgrading" != "null" ]]; then
        check_fail "$hub_name: Cluster upgrade in progress (version: $ocp_version)"
        echo -e "${RED}       Message: $upgrading${NC}"
    else
        check_pass "$hub_name: Cluster is stable (version: $ocp_version, no upgrade in progress)"
    fi

    return 0
}

# =============================================================================
# Summary Output
# =============================================================================

# Print the validation/verification summary
# Usage: print_summary "preflight" | print_summary "postflight"
print_summary() {
    local mode="${1:-preflight}"
    
    echo ""
    if [[ "$mode" == "preflight" ]]; then
        echo "╔════════════════════════════════════════════════════════════╗"
        echo "║   Validation Summary                                       ║"
        echo "╚════════════════════════════════════════════════════════════╝"
    else
        echo "╔════════════════════════════════════════════════════════════╗"
        echo "║   Verification Summary                                     ║"
        echo "╚════════════════════════════════════════════════════════════╝"
    fi
    
    echo ""
    echo -e "Total Checks:    $TOTAL_CHECKS"
    echo -e "${GREEN}Passed:          $PASSED_CHECKS${NC}"
    echo -e "${RED}Failed:          $FAILED_CHECKS${NC}"
    echo -e "${YELLOW}Warnings:        $WARNING_CHECKS${NC}"
    echo ""

    # Print failed checks if any
    if [[ $FAILED_CHECKS -gt 0 ]]; then
        echo -e "${RED}Failed Checks:${NC}"
        for msg in "${FAILED_MESSAGES[@]}"; do
            echo -e "${RED}  - $msg${NC}"
        done
        echo ""
    fi

    # Print warnings if any
    if [[ $WARNING_CHECKS -gt 0 ]]; then
        echo -e "${YELLOW}Warnings:${NC}"
        for msg in "${WARNING_MESSAGES[@]}"; do
            echo -e "${YELLOW}  - $msg${NC}"
        done
        echo ""
    fi

    # Final result
    if [[ $FAILED_CHECKS -eq 0 ]]; then
        echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        if [[ "$mode" == "preflight" ]]; then
            echo -e "${GREEN}✓ ALL CRITICAL CHECKS PASSED${NC}"
        else
            echo -e "${GREEN}✓ SWITCHOVER VERIFICATION PASSED${NC}"
        fi
        echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo ""
        
        if [[ "$mode" == "preflight" ]]; then
            echo "You are ready to proceed with the switchover."
        else
            echo "The ACM switchover appears to have completed successfully."
        fi
        echo ""
        
        if [[ $WARNING_CHECKS -gt 0 ]]; then
            if [[ "$mode" == "preflight" ]]; then
                echo -e "${YELLOW}Note: $WARNING_CHECKS warning(s) detected. Review them before proceeding.${NC}"
            else
                echo -e "${YELLOW}Note: $WARNING_CHECKS warning(s) detected. Review them above.${NC}"
                echo -e "${YELLOW}Some items may need time to stabilize (e.g., metrics collection).${NC}"
            fi
            echo ""
        fi
        
        if [[ "$mode" == "postflight" ]]; then
            echo "Recommended next steps:"
            echo "  1. Verify Grafana dashboards show recent metrics (wait 5-10 minutes)"
            echo "  2. Test cluster management operations (create/update policies, etc.)"
            echo "  3. Monitor for 24 hours before decommissioning old hub"
            echo "  4. Inform stakeholders that switchover is complete"
            echo ""
        fi
        
        return 0
    else
        echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        if [[ "$mode" == "preflight" ]]; then
            echo -e "${RED}✗ VALIDATION FAILED${NC}"
        else
            echo -e "${RED}✗ VERIFICATION FAILED${NC}"
        fi
        echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo ""
        
        if [[ "$mode" == "preflight" ]]; then
            echo "Please fix the failed checks before proceeding with switchover."
        else
            echo "Critical issues detected. Review the failed checks above."
            echo ""
            echo "Common issues and solutions:"
            echo "  - Clusters not Available: Wait 5-10 minutes for reconnection"
            echo "  - Restore not Finished: Check restore status with 'oc describe restore'"
            echo "  - Observability pods failing: Verify observatorium-api was restarted"
            echo "  - BackupSchedule paused: Unpause with 'oc patch backupschedule ...'"
            echo ""
            echo "If issues persist, consider rollback procedure in the runbook."
        fi
        echo ""
        
        return 1
    fi
}
