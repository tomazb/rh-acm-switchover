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

# Derive approximate schedule interval (seconds) from a cron expression.
# Mirrors the Python _parse_cron_interval_seconds implementation in modules/finalization.py
_derive_backup_interval_seconds() {
    local cron_expr="$1"
    local minute hour dom month dow

    # Split into 5 fields; if not exactly 5, bail out
    read -r minute hour dom month dow <<<"$cron_expr" || return 0
    if [[ -z "$dow" ]]; then
        echo ""
        return 0
    fi

    _parse_every_field() {
        local field="$1"
        if [[ "$field" =~ ^\*/([0-9]+)$ ]]; then
            local value="${BASH_REMATCH[1]}"
            if (( value > 0 )); then
                echo "$value"
                return 0
            fi
        fi
        echo ""
        return 0
    }

    _is_number_field() {
        [[ "$1" =~ ^[0-9]+$ ]]
    }

    local every_minute every_hour every_day

    every_minute=$(_parse_every_field "$minute")
    if [[ -n "$every_minute" && "$hour" == "*" && "$dom" == "*" && "$month" == "*" && "$dow" == "*" ]]; then
        echo $(( every_minute * 60 ))
        return 0
    fi

    every_hour=$(_parse_every_field "$hour")
    if [[ -n "$every_hour" && "$dom" == "*" && "$month" == "*" && "$dow" == "*" ]] && _is_number_field "$minute"; then
        echo $(( every_hour * 3600 ))
        return 0
    fi

    every_day=$(_parse_every_field "$dom")
    if [[ -n "$every_day" && "$month" == "*" && "$dow" == "*" ]] && _is_number_field "$minute" && _is_number_field "$hour"; then
        echo $(( every_day * 86400 ))
        return 0
    fi

    if [[ "$dom" == "*" && "$month" == "*" && "$dow" == "*" ]] && _is_number_field "$minute" && _is_number_field "$hour"; then
        echo 86400
        return 0
    fi

    if [[ "$dom" == "*" && "$month" == "*" ]] && _is_number_field "$minute" && _is_number_field "$hour" && [[ "$dow" =~ ^[0-9]+$ ]]; then
        echo $(( 7 * 86400 ))
        return 0
    fi

    if _is_number_field "$minute" && _is_number_field "$hour" && [[ "$dom" =~ ^[0-9]+$ ]] && [[ "$month" == "*" && "$dow" == "*" ]]; then
        echo $(( 30 * 86400 ))
        return 0
    fi

    echo ""
}

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

# Check nodes health on a given context
# Usage: check_nodes "$CONTEXT" "Hub name"
# Note: Always returns 0 to prevent aborting the script (set -e), allowing remaining checks to run
check_nodes() {
    local context="$1"
    local hub_name="$2"
    local nodes_json
    local oc_stderr_file
    oc_stderr_file="$(mktemp)"

    if ! nodes_json=$("$CLUSTER_CLI_BIN" --context="$context" get nodes -o json 2>"$oc_stderr_file"); then
        local oc_error
        oc_error="$(<"$oc_stderr_file")"
        rm -f "$oc_stderr_file"

        if [[ -n "$oc_error" ]]; then
            check_fail "$hub_name: Could not retrieve nodes: $oc_error"
        else
            check_fail "$hub_name: Could not retrieve nodes (insufficient permissions or cluster issue)"
        fi
        return 0
    fi

    rm -f "$oc_stderr_file"
    
    if [[ -z "$nodes_json" ]]; then
        check_fail "$hub_name: Could not retrieve nodes (insufficient permissions or cluster issue)"
        return 0
    fi
    
    local total ready not_ready
    total=$(echo "$nodes_json" | jq -r '.items | length' 2>/dev/null || echo "0")
    ready=$(echo "$nodes_json" | jq -r '[.items[] | select(.status.conditions[]? | select(.type=="Ready" and .status=="True"))] | length' 2>/dev/null || echo "0")
    not_ready=$((total - ready))
    
    if [[ $total -eq 0 ]]; then
        check_fail "$hub_name: Could not retrieve nodes (insufficient permissions or cluster issue)"
        return 0
    elif [[ $ready -eq $total ]]; then
        check_pass "$hub_name: All $total node(s) are Ready"
        return 0
    else
        check_fail "$hub_name: $not_ready of $total node(s) are not Ready"
        return 0
    fi
}

# =============================================================================
# Data Protection Helpers
# =============================================================================

# Check DataProtectionApplication status on a given context
# Usage: check_dpa_status "$CONTEXT" "Hub name"
check_dpa_status() {
    local ctx="$1"
    local hub_name="$2"

    local dpa_count
    dpa_count=$("$CLUSTER_CLI_BIN" --context="$ctx" get $RES_DPA -n "$BACKUP_NAMESPACE" --no-headers 2>/dev/null | wc -l || echo "0")
    
    if [[ $dpa_count -gt 0 ]]; then
        local dpa_name dpa_reconciled
        dpa_name=$("$CLUSTER_CLI_BIN" --context="$ctx" get $RES_DPA -n "$BACKUP_NAMESPACE" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
        dpa_reconciled=$("$CLUSTER_CLI_BIN" --context="$ctx" get $RES_DPA "$dpa_name" -n "$BACKUP_NAMESPACE" -o jsonpath='{.status.conditions[?(@.type=="Reconciled")].status}' 2>/dev/null || echo "")
        
        if [[ "$dpa_reconciled" == "True" ]]; then
            check_pass "$hub_name: DataProtectionApplication '$dpa_name' is reconciled"
        else
            check_fail "$hub_name: DataProtectionApplication '$dpa_name' exists but not reconciled"
        fi
    else
        check_fail "$hub_name: No DataProtectionApplication found"
    fi
}

# Check BackupStorageLocation status on a given context
# Usage: check_bsl_status "$CONTEXT" "Hub name"
check_bsl_status() {
    local ctx="$1"
    local hub_name="$2"

    local bsl_count
    bsl_count=$("$CLUSTER_CLI_BIN" --context="$ctx" get $RES_BSL -n "$BACKUP_NAMESPACE" --no-headers 2>/dev/null | wc -l || echo "0")
    
    if [[ $bsl_count -gt 0 ]]; then
        local bsl_name bsl_phase
        bsl_name=$("$CLUSTER_CLI_BIN" --context="$ctx" get $RES_BSL -n "$BACKUP_NAMESPACE" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
        bsl_phase=$("$CLUSTER_CLI_BIN" --context="$ctx" get $RES_BSL "$bsl_name" -n "$BACKUP_NAMESPACE" -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
        
        if [[ "$bsl_phase" == "Available" ]]; then
            check_pass "$hub_name: BackupStorageLocation '$bsl_name' is Available"
        else
            check_fail "$hub_name: BackupStorageLocation '$bsl_name' phase is '$bsl_phase' (expected: Available)"
            echo -e "${RED}       Unavailable BSL means restores cannot proceed${NC}"
            
            local bsl_conditions
            bsl_conditions=$("$CLUSTER_CLI_BIN" --context="$ctx" get $RES_BSL "$bsl_name" -n "$BACKUP_NAMESPACE" -o json 2>/dev/null | \
                jq -r '.status.conditions // [] | map("\(.type)=\(.status) reason=\(.reason // "n/a") msg=\(.message // "n/a")") | join("; ")' || echo "")
            if [[ -n "$bsl_conditions" ]]; then
                echo -e "${RED}       BSL conditions: $bsl_conditions${NC}"
            else
                echo -e "${YELLOW}       BSL conditions: none reported${NC}"
            fi
        fi
    else
        check_fail "$hub_name: No BackupStorageLocation found"
    fi
}

# Check Velero pods on a given context
# Usage: check_velero_pods "$CONTEXT" "Hub name"
check_velero_pods() {
    local ctx="$1"
    local hub_name="$2"

    if "$CLUSTER_CLI_BIN" --context="$ctx" get namespace "$BACKUP_NAMESPACE" &> /dev/null; then
        local velero_pods
        velero_pods=$("$CLUSTER_CLI_BIN" --context="$ctx" get pods -n "$BACKUP_NAMESPACE" -l app.kubernetes.io/name=velero --no-headers 2>/dev/null | wc -l || echo "0")
        if [[ $velero_pods -gt 0 ]]; then
            check_pass "$hub_name: OADP operator installed ($velero_pods Velero pod(s))"
        else
            check_fail "$hub_name: OADP namespace exists but no Velero pods found"
        fi
    else
        check_fail "$hub_name: OADP operator not installed ($BACKUP_NAMESPACE namespace missing)"
    fi
}

# =============================================================================
# Timestamp Helpers
# =============================================================================

# Format age in seconds as human-readable string
# Usage: format_age_display SECONDS
# Returns: "30s", "5m", "2h30m", "3d12h"
format_age_display() {
    local age_seconds="$1"
    
    if [[ $age_seconds -lt 60 ]]; then
        echo "${age_seconds}s"
    elif [[ $age_seconds -lt 3600 ]]; then
        echo "$((age_seconds / 60))m"
    elif [[ $age_seconds -lt 86400 ]]; then
        local hours=$((age_seconds / 3600))
        local minutes=$(( (age_seconds % 3600) / 60 ))
        echo "${hours}h${minutes}m"
    else
        local days=$((age_seconds / 86400))
        local hours=$(( (age_seconds % 86400) / 3600 ))
        echo "${days}d${hours}h"
    fi
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

# =============================================================================
# GitOps Detection Helpers
# =============================================================================

# Indexed arrays for collecting GitOps-managed resources
# Format: GITOPS_DETECTED_RESOURCES[index]="[context] namespace/Kind/name"
#         GITOPS_DETECTED_MARKERS[index]="marker1,marker2,..."
GITOPS_DETECTED_RESOURCES=()
GITOPS_DETECTED_MARKERS=()
GITOPS_DETECTION_ENABLED=1  # 1=enabled, 0=disabled
GITOPS_MAX_DISPLAY_PER_KIND=10

# Disable GitOps detection (used with --skip-gitops-check)
# Usage: disable_gitops_detection
disable_gitops_detection() {
    GITOPS_DETECTION_ENABLED=0
}

# Check if a resource has GitOps markers (ArgoCD, Flux)
# Usage: detect_gitops_markers "$RESOURCE_JSON"
# Returns: comma-separated list of markers, or empty string if none
# Example: detect_gitops_markers "$(oc get backupschedule foo -o json)"
detect_gitops_markers() {
    local resource_json="$1"
    local markers=""

    if [[ -z "$resource_json" ]]; then
        echo ""
        return 0
    fi

    # Extract labels and annotations
    local labels annotations
    labels=$(echo "$resource_json" | jq -r '.metadata.labels // {} | to_entries | .[] | "\(.key)=\(.value)"' 2>/dev/null || echo "")
    annotations=$(echo "$resource_json" | jq -r '.metadata.annotations // {} | to_entries | .[] | "\(.key)=\(.value)"' 2>/dev/null || echo "")

    # Check labels for GitOps markers
    while IFS= read -r label; do
        [[ -z "$label" ]] && continue
        local key="${label%%=*}"
        local value="${label#*=}"

        # Argo CD instance tracking keys (explicit match)
        if [[ "$key" == "app.kubernetes.io/instance" ]]; then
            [[ -n "$markers" ]] && markers+=","
            markers+="label:${key} (UNRELIABLE)"
            continue
        fi
        if [[ "$key" == "argocd.argoproj.io/instance" ]]; then
            [[ -n "$markers" ]] && markers+=","
            markers+="label:${key}"
            continue
        fi

        # managed-by detection (exact value match to avoid substring false positives)
        if [[ "$key" == "app.kubernetes.io/managed-by" ]]; then
            local value_lower
            value_lower=$(echo "$value" | tr '[:upper:]' '[:lower:]')
            if [[ "$value_lower" == "argocd" ]] || [[ "$value_lower" == "flux" ]] || [[ "$value_lower" == "fluxcd" ]]; then
                [[ -n "$markers" ]] && markers+=","
                markers+="label:app.kubernetes.io/managed-by"
            fi
        else
            local label_lower
            label_lower=$(echo "$label" | tr '[:upper:]' '[:lower:]')

            # ArgoCD detection
            if [[ "$label_lower" == *"argocd"* ]] || [[ "$label_lower" == *"argoproj.io"* ]]; then
                [[ -n "$markers" ]] && markers+=","
                markers+="label:${key}"
            # Flux detection
            elif [[ "$label_lower" == *"fluxcd.io"* ]] || [[ "$label_lower" == *"toolkit.fluxcd.io"* ]]; then
                [[ -n "$markers" ]] && markers+=","
                markers+="label:${key}"
            fi
        fi
    done <<< "$labels"

    # Check annotations for GitOps markers
    while IFS= read -r annotation; do
        [[ -z "$annotation" ]] && continue
        local key="${annotation%%=*}"
        local annotation_lower
        annotation_lower=$(echo "$annotation" | tr '[:upper:]' '[:lower:]')

        # Argo CD instance tracking key (explicit match)
        if [[ "$key" == "app.kubernetes.io/instance" ]]; then
            [[ -n "$markers" ]] && markers+=","
            markers+="annotation:${key} (UNRELIABLE)"
            continue
        fi
        if [[ "$key" == "argocd.argoproj.io/instance" ]]; then
            [[ -n "$markers" ]] && markers+=","
            markers+="annotation:${key}"
            continue
        fi

        # ArgoCD detection
        if [[ "$annotation_lower" == *"argocd"* ]] || [[ "$annotation_lower" == *"argoproj.io"* ]]; then
            [[ -n "$markers" ]] && markers+=","
            markers+="annotation:${key}"
        # Flux detection
        elif [[ "$annotation_lower" == *"fluxcd.io"* ]] || [[ "$annotation_lower" == *"toolkit.fluxcd.io"* ]]; then
            [[ -n "$markers" ]] && markers+=","
            markers+="annotation:${key}"
        fi
    done <<< "$annotations"

    # Deduplicate and return comma-separated (deterministic order)
    if [[ -n "$markers" ]]; then
        echo "$markers" | tr ',' '\n' | sort -u | paste -sd ',' -
    fi
}

# Collect a GitOps-managed resource for later reporting
# Usage: collect_gitops_markers "context" "namespace" "Kind" "name" "markers"
# Example: collect_gitops_markers "primary" "open-cluster-management-backup" "BackupSchedule" "acm-backup" "label:managed-by"
collect_gitops_markers() {
    local context="$1"
    local namespace="$2"
    local kind="$3"
    local name="$4"
    local markers="$5"

    # Skip if detection is disabled or no markers
    if [[ $GITOPS_DETECTION_ENABLED -eq 0 ]] || [[ -z "$markers" ]]; then
        return 0
    fi

    # Build resource identifier
    local resource_id
    if [[ -n "$namespace" ]]; then
        resource_id="[${context}] ${namespace}/${kind}/${name}"
    else
        resource_id="[${context}] ${kind}/${name}"
    fi

    # Add to arrays
    GITOPS_DETECTED_RESOURCES+=("$resource_id")
    GITOPS_DETECTED_MARKERS+=("$markers")
}

# Print consolidated GitOps detection report
# Usage: print_gitops_report
print_gitops_report() {
    # Skip if detection is disabled
    if [[ $GITOPS_DETECTION_ENABLED -eq 0 ]]; then
        return 0
    fi

    local count=${#GITOPS_DETECTED_RESOURCES[@]}

    # Skip if no detections
    if [[ $count -eq 0 ]]; then
        return 0
    fi

    echo ""
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    if [[ $count -eq 1 ]]; then
        echo -e "${YELLOW}GitOps-related markers detected ($count warning)${NC}"
    else
        echo -e "${YELLOW}GitOps-related markers detected ($count warnings)${NC}"
    fi
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}Coordinate changes with GitOps to avoid drift after switchover.${NC}"
    echo ""

    # Sort resources for consistent output (matches Python implementation)
    # Format: index:resource:markers, then sort by resource identifier
    local -a sorted_entries
    for i in "${!GITOPS_DETECTED_RESOURCES[@]}"; do
        sorted_entries+=("$i|${GITOPS_DETECTED_RESOURCES[$i]}|${GITOPS_DETECTED_MARKERS[$i]}")
    done

    # Sort by resource identifier (field 2), then rebuild arrays
    local -a sorted_resources
    local -a sorted_markers
    local -a sorted_indices

    while IFS='|' read -r idx resource markers; do
        sorted_indices+=("$idx")
        sorted_resources+=("$resource")
        sorted_markers+=("$markers")
    done < <(printf '%s\n' "${sorted_entries[@]}" | sort -t'|' -k2)

    # Replace original arrays with sorted versions
    GITOPS_DETECTED_RESOURCES=("${sorted_resources[@]}")
    GITOPS_DETECTED_MARKERS=("${sorted_markers[@]}")

    # Group by kind for summarization
    declare -A kind_counts
    declare -A kind_displayed
    local i

    # Count resources by kind (arrays are now sorted)
    for i in "${!GITOPS_DETECTED_RESOURCES[@]}"; do
        local resource="${GITOPS_DETECTED_RESOURCES[$i]}"
        # Extract kind from resource identifier
        # Format: "[context] namespace/Kind/name" or "[context] Kind/name"
        local kind
        kind=$(echo "$resource" | sed -E 's/^\[[^]]+\] ([^/]+\/)?([^/]+)\/[^/]+$/\2/')
        # Use default expansion to avoid unbound variable errors under set -u
        if [[ -z "${kind_counts[$kind]:-}" ]]; then
            kind_counts[$kind]=0
            kind_displayed[$kind]=0
        fi
        ((++kind_counts[$kind])) || true
    done

    # Display resources with truncation per kind (arrays are now sorted)
    for i in "${!GITOPS_DETECTED_RESOURCES[@]}"; do
        local resource="${GITOPS_DETECTED_RESOURCES[$i]}"
        local markers="${GITOPS_DETECTED_MARKERS[$i]}"

        # Extract kind
        local kind
        kind=$(echo "$resource" | sed -E 's/^\[[^]]+\] ([^/]+\/)?([^/]+)\/[^/]+$/\2/')

        # Check if we've hit the display limit for this kind
        # Use default expansion to avoid unbound variable errors under set -u
        if [[ ${kind_displayed[$kind]:-0} -ge $GITOPS_MAX_DISPLAY_PER_KIND ]]; then
            # Only show "and X more" message once per kind
            if [[ ${kind_displayed[$kind]:-0} -eq $GITOPS_MAX_DISPLAY_PER_KIND ]]; then
                local remaining=$((${kind_counts[$kind]:-0} - GITOPS_MAX_DISPLAY_PER_KIND))
                echo -e "${YELLOW}  ... and $remaining more ${kind}(s)${NC}"
                # Increment to prevent showing message again
                ((kind_displayed[$kind]++)) || true
            fi
            continue
        fi

        echo -e "${YELLOW}${resource}${NC}"
        # Print each marker
        IFS=',' read -ra marker_array <<< "$markers"
        for marker in "${marker_array[@]}"; do
            echo -e "${YELLOW}  → ${marker}${NC}"
        done

        ((kind_displayed[$kind]++)) || true
    done

    echo ""
}

# Check ArgoCD instances and ACM-related resources managed by ArgoCD Applications.
# Supports both operator install (argocds.argoproj.io) and vanilla Argo CD (applications.argoproj.io only).
# Usage: check_argocd_acm_resources "context" "label"
check_argocd_acm_resources() {
    local context="$1"
    local label="$2"

    if [[ $GITOPS_DETECTION_ENABLED -eq 0 ]]; then
        return 0
    fi

    # Need at least applications.argoproj.io to do any Argo CD check
    if ! "$CLUSTER_CLI_BIN" --context="$context" get crd applications.argoproj.io &>/dev/null; then
        check_pass "$label: Argo CD Applications CRD not found (skipping ArgoCD GitOps check)"
        return 0
    fi

    local ns_regex='^(open-cluster-management($|-.*)|open-cluster-management-backup$|open-cluster-management-observability$|open-cluster-management-global-set$|multicluster-engine$|local-cluster)$'
    local kinds_json='["MultiClusterHub","MultiClusterEngine","MultiClusterObservability","ManagedCluster","ManagedClusterSet","ManagedClusterSetBinding","Placement","PlacementBinding","Policy","PolicySet","BackupSchedule","Restore","DataProtectionApplication","ClusterDeployment"]'

    local found_any=0
    local has_argocds_crd=0
    local argocd_count=0

    # Operator install: argocds.argoproj.io exists -> list instances and scan apps per instance namespace
    if "$CLUSTER_CLI_BIN" --context="$context" get crd argocds.argoproj.io &>/dev/null; then
        has_argocds_crd=1
        local argocd_json
        argocd_json=$("$CLUSTER_CLI_BIN" --context="$context" get argocds.argoproj.io -A -o json 2>/dev/null || echo '{"items":[]}')
        argocd_count=$(echo "$argocd_json" | jq '.items | length' 2>/dev/null || echo 0)

        if [[ $argocd_count -gt 0 ]]; then
            echo ""
            echo -e "${BLUE}ArgoCD instances on ${label}:${NC}"
            echo "$argocd_json" | jq -r '.items[] | "  - \(.metadata.namespace)/\(.metadata.name)"'
            echo ""
        fi

        while IFS=$'\t' read -r argocd_ns _; do
            local apps_json
            apps_json=$("$CLUSTER_CLI_BIN" --context="$context" -n "$argocd_ns" get applications.argoproj.io -o json 2>/dev/null || echo '{"items":[]}')
            local app_output
            app_output=$(echo "$apps_json" | jq -r --arg ns_regex "$ns_regex" --argjson kinds "$kinds_json" '
                    def fmt($r):
                        ($r.namespace // "-") as $ns
                        | if $ns == "-" then
                                "    - \($r.kind) \($r.name)"
                            else
                                "    - \($r.kind) \($ns)/\($r.name)"
                            end;
                    (.items // [])
                    | map(select(type=="object"))
                    | .[]
                    | . as $app
                    | ($app.status.resources // [])
                    | if type=="array" then . else [] end
                    | map(select(type=="object") | select(has("kind")))
                    | map(select(((.namespace // "") | test($ns_regex)) or (.kind as $k | ($kinds | index($k)))))
                    | sort_by(.kind,.namespace,.name)
                    | if length>0 then
                            ("\n  Application: \($app.metadata.name) (\(length) resources)"),
                            (.[0:5] | map(fmt(.))[]),
                            (if length > 5 then "    - ... and \(length - 5) more" else empty end)
                        else empty end
            ')
            if [[ -n "$app_output" ]]; then
                found_any=1
                echo -e "${YELLOW}ACM resources managed by ArgoCD in namespace ${argocd_ns}:${NC}"
                echo "$app_output"
            fi
        done < <(echo "$argocd_json" | jq -r '.items[] | "\(.metadata.namespace)\t\(.metadata.name)"')
    fi

    # Vanilla Argo CD (or no ArgoCD instances): scan all Applications cluster-wide
    if [[ $found_any -eq 0 && ( $has_argocds_crd -eq 0 || $argocd_count -eq 0 ) ]]; then
        local all_apps_json
        all_apps_json=$("$CLUSTER_CLI_BIN" --context="$context" get applications.argoproj.io -A -o json 2>/dev/null || echo '{"items":[]}')
        local app_count
        app_count=$(echo "$all_apps_json" | jq '.items | length' 2>/dev/null || echo 0)

        if [[ $app_count -eq 0 ]]; then
            check_pass "$label: No Argo CD Applications found"
            return 0
        fi

        echo ""
        echo -e "${BLUE}Argo CD on ${label}:${NC}"
        if [[ $has_argocds_crd -eq 0 ]]; then
            echo -e "  (Vanilla install: applications.argoproj.io only, no argocds.argoproj.io)"
        else
            echo -e "  (No ArgoCD instances detected; scanning Applications cluster-wide)"
        fi
        echo ""

        local app_output
        app_output=$(echo "$all_apps_json" | jq -r --arg ns_regex "$ns_regex" --argjson kinds "$kinds_json" '
                def fmt($r):
                    ($r.namespace // "-") as $ns
                    | if $ns == "-" then
                            "    - \($r.kind) \($r.name)"
                        else
                            "    - \($r.kind) \($ns)/\($r.name)"
                        end;
                (.items // [])
                | map(select(type=="object"))
                | .[]
                | . as $app
                | ($app.status.resources // [])
                | if type=="array" then . else [] end
                | map(select(type=="object") | select(has("kind")))
                | map(select(((.namespace // "") | test($ns_regex)) or (.kind as $k | ($kinds | index($k)))))
                | sort_by(.kind,.namespace,.name)
                | if length>0 then
                        ("\n  Application: \($app.metadata.namespace)/\($app.metadata.name) (\(length) resources)"),
                        (.[0:5] | map(fmt(.))[]),
                        (if length > 5 then "    - ... and \(length - 5) more" else empty end)
                    else empty end
        ')
        if [[ -n "$app_output" ]]; then
            found_any=1
            echo -e "${YELLOW}ACM resources managed by Argo CD Applications:${NC}"
            echo "$app_output"
        fi
    fi

    if [[ $found_any -eq 1 ]]; then
        check_warn "$label: ACM resources detected in ArgoCD Applications"
        echo -e "${YELLOW}       GitOps reconciliation may override switchover changes and cause drift.${NC}"
        echo -e "${YELLOW}       Pause/scope ArgoCD apps for ACM resources before switchover.${NC}"
    else
        check_pass "$label: No ACM resources detected in ArgoCD Applications"
    fi
}
