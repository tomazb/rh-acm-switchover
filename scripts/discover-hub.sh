#!/bin/bash
#
# ACM Hub Discovery Script
#
# This script discovers available Kubernetes contexts, detects which is the
# primary hub vs secondary/standby, and proposes the appropriate preflight
# or postflight check command based on the detected state.
#
# IDEMPOTENT: This script is read-only and can be run multiple times without
# side effects. It performs only GET operations and does not modify cluster state.
#
# Usage:
#   ./scripts/discover-hub.sh [--contexts ctx1,ctx2] [--run] [--timeout <seconds>]
#
# Exit codes:
#   0 - Discovery completed successfully
#   1 - No ACM hubs found or error
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

# =============================================================================
# Script-specific variables
# =============================================================================
CONTEXTS=""
RUN_PROPOSED=false
CONNECTION_TIMEOUT=5
VERBOSE=false
AUTO_DISCOVER=false

# Discovered hub information (parallel arrays)
declare -a HUB_CONTEXTS=()
declare -a HUB_ROLES=()           # "primary", "secondary", "standby", "unknown"
declare -a HUB_STATES=()          # Human-readable state description
declare -a HUB_MC_COUNTS=()       # Number of available managed clusters
declare -a HUB_BACKUP_STATES=()   # BackupSchedule state
declare -a HUB_VERSIONS=()        # ACM version
declare -a HUB_OCP_VERSIONS=()    # OCP / Kubernetes server version (if available)
declare -a HUB_OCP_CHANNELS=()    # OpenShift update channel (if available)
declare -a HUB_KLUSTERLET_COUNTS=()  # Number of clusters with klusterlet pointing to this hub
declare -a ALL_MANAGED_CLUSTERS=()   # All managed cluster contexts discovered

# =============================================================================
# Helper Functions
# =============================================================================

# Print usage information
usage() {
    cat << EOF
Usage: $0 [OPTIONS]

Discover ACM hubs from available Kubernetes contexts and propose appropriate checks.

Options:
  --auto                      Auto-discover ACM hubs from all kubeconfig contexts
  --contexts <ctx1,ctx2,...>  Comma-separated list of specific contexts to check
  --run                       Execute the proposed check command
  --verbose, -v               Show detailed cluster status for each hub
  --timeout <seconds>         Connection timeout per context (default: 5)
  --help, -h                  Show this help message

Examples:
  # Auto-discover ACM hubs from all contexts
  $0 --auto

  # Check specific contexts only
  $0 --contexts hub1,hub2

  # Auto-discover and immediately run the proposed check
  $0 --auto --run

  # Show detailed cluster status
  $0 --auto --verbose
EOF
    exit "$EXIT_SUCCESS"
}

# Test if a context is reachable
# Returns 0 if reachable, 1 if not
test_context_reachable() {
    local ctx="$1"
    timeout "${CONNECTION_TIMEOUT}s" "$CLUSTER_CLI_BIN" --context="$ctx" cluster-info &>/dev/null
    return $?
}

# Check if context has ACM installed (backup namespace exists)
# Returns 0 if ACM hub, 1 if not
is_acm_hub() {
    local ctx="$1"
    "$CLUSTER_CLI_BIN" --context="$ctx" get namespace "$BACKUP_NAMESPACE" &>/dev/null && \
    "$CLUSTER_CLI_BIN" --context="$ctx" get namespace "$ACM_NAMESPACE" &>/dev/null
    return $?
}

# Get ACM version for a context
get_acm_version() {
    local ctx="$1"
    local version
    version=$("$CLUSTER_CLI_BIN" --context="$ctx" get $RES_MCH -n "$ACM_NAMESPACE" \
        -o jsonpath='{.items[0].status.currentVersion}' 2>/dev/null || echo "unknown")
    echo "$version"
}

# Get OCP / Kubernetes server version (prefer ClusterVersion on OpenShift)
get_ocp_version() {
    local ctx="$1"
    local cv_ver

    # Try ClusterVersion resource (OpenShift)
    cv_ver=$("$CLUSTER_CLI_BIN" --context="$ctx" get clusterversion version -o jsonpath='{.status.desired.version}' 2>/dev/null || echo "")
    if [[ -n "$cv_ver" ]]; then
        echo "$cv_ver"
        return
    fi

    # Fallback to server version from the CLI (using JSON output for k8s 1.29+ compatibility)
    local server_ver
    server_ver=$("$CLUSTER_CLI_BIN" --context="$ctx" version -o json 2>/dev/null | jq -r '.serverVersion.gitVersion // empty' || echo "")
    if [[ -n "$server_ver" ]]; then
        echo "$server_ver"
        return
    fi

    echo "unknown"
}

# Get OpenShift update channel (if available)
get_ocp_channel() {
    local ctx="$1"
    local channel
    channel=$("$CLUSTER_CLI_BIN" --context="$ctx" get clusterversion version -o jsonpath='{.spec.channel}' 2>/dev/null || echo "")
    if [[ -n "$channel" ]]; then
        echo "$channel"
    else
        echo "n/a"
    fi
}

# Get BackupSchedule state for a context
# Returns: "active", "paused", "collision", or "none"
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
    else
        echo "active"
    fi
}

# Get restore state for a context
# Returns: "passive-sync", "full-restore", "finished", "none"
get_restore_state() {
    local ctx="$1"
    
    # Get latest restore
    local restore_name
    restore_name=$("$CLUSTER_CLI_BIN" --context="$ctx" get $RES_RESTORE -n "$BACKUP_NAMESPACE" \
        --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name}' 2>/dev/null || echo "")
    
    if [[ -z "$restore_name" ]]; then
        echo "none"
        return
    fi
    
    local phase
    phase=$("$CLUSTER_CLI_BIN" --context="$ctx" get $RES_RESTORE "$restore_name" -n "$BACKUP_NAMESPACE" \
        -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
    
    local sync_enabled
    sync_enabled=$("$CLUSTER_CLI_BIN" --context="$ctx" get $RES_RESTORE "$restore_name" -n "$BACKUP_NAMESPACE" \
        -o jsonpath='{.spec.syncRestoreWithNewBackups}' 2>/dev/null || echo "false")
    
    if [[ "$sync_enabled" == "true" ]] && [[ "$phase" == "Enabled" ]]; then
        echo "passive-sync"
    elif [[ "$phase" == "Finished" ]] || [[ "$phase" == "Completed" ]]; then
        echo "finished"
    elif [[ -n "$phase" ]]; then
        echo "in-progress:$phase"
    else
        echo "none"
    fi
}

# Get count of available managed clusters
get_available_mc_count() {
    local ctx="$1"
    
    "$CLUSTER_CLI_BIN" --context="$ctx" get $RES_MANAGED_CLUSTER -o json 2>/dev/null | \
        jq -r --arg LOCAL "$LOCAL_CLUSTER_NAME" \
        '[.items[] | select(.metadata.name != $LOCAL) | select(.status.conditions[]? | select(.type=="ManagedClusterConditionAvailable" and .status=="True"))] | length' \
        2>/dev/null || echo "0"
}

# Get list of managed cluster names (excluding local-cluster)
get_managed_cluster_names() {
    local ctx="$1"
    
    "$CLUSTER_CLI_BIN" --context="$ctx" get $RES_MANAGED_CLUSTER -o json 2>/dev/null | \
        jq -r --arg LOCAL "$LOCAL_CLUSTER_NAME" \
        '.items[] | select(.metadata.name != $LOCAL) | .metadata.name' \
        2>/dev/null || echo ""
}

# Check which hub a klusterlet is connected to
# Returns the API server URL the klusterlet is using
get_klusterlet_hub_server() {
    local mc_ctx="$1"
    
    # Try to get the hub-kubeconfig-secret (actual connection)
    local server
    server=$("$CLUSTER_CLI_BIN" --context="$mc_ctx" get secret -n open-cluster-management-agent hub-kubeconfig-secret \
        -o jsonpath='{.data.kubeconfig}' 2>/dev/null | base64 -d 2>/dev/null | grep -o 'server: [^ ]*' | head -1 | cut -d' ' -f2 || echo "")
    
    if [[ -z "$server" ]]; then
        # Fallback to bootstrap secret
        server=$("$CLUSTER_CLI_BIN" --context="$mc_ctx" get secret -n open-cluster-management-agent bootstrap-hub-kubeconfig \
            -o jsonpath='{.data.kubeconfig}' 2>/dev/null | base64 -d 2>/dev/null | grep -o 'server: [^ ]*' | head -1 | cut -d' ' -f2 || echo "")
    fi
    
    echo "$server"
}

# Get the API server URL for a hub context
get_hub_api_server() {
    local ctx="$1"
    "$CLUSTER_CLI_BIN" --context="$ctx" cluster-info 2>/dev/null | grep -o 'https://[^ ]*' | head -1 || echo ""
}

# Verify klusterlet connections for all managed clusters
# This is called after initial discovery to resolve ambiguous cases
# Updates HUB_KLUSTERLET_COUNTS array
verify_klusterlet_connections() {
    local -a hub_servers=()
    local -a klusterlet_counts=()
    
    # Initialize counts and get hub API servers
    for i in "${!HUB_CONTEXTS[@]}"; do
        local ctx="${HUB_CONTEXTS[$i]}"
        local server
        server=$(get_hub_api_server "$ctx")
        hub_servers+=("$server")
        klusterlet_counts+=(0)
    done
    
    # Check if we need to verify (both hubs have available clusters)
    local need_verification=false
    local available_count=0
    for i in "${!HUB_CONTEXTS[@]}"; do
        local mc_count="${HUB_MC_COUNTS[$i]}"
        local available="${mc_count%%/*}"
        if [[ "$available" -gt 0 ]]; then
            available_count=$((available_count + 1))
        fi
    done
    
    if [[ "$available_count" -lt 2 ]]; then
        # Only one hub has clusters, no need to verify
        for i in "${!HUB_CONTEXTS[@]}"; do
            HUB_KLUSTERLET_COUNTS+=("${HUB_MC_COUNTS[$i]%%/*}")
        done
        return
    fi
    
    echo -e "\n  ${YELLOW}Both hubs report available clusters - verifying klusterlet connections...${NC}"
    
    # Get unique list of managed clusters from all hubs
    declare -A seen_clusters
    for i in "${!HUB_CONTEXTS[@]}"; do
        local ctx="${HUB_CONTEXTS[$i]}"
        local clusters
        clusters=$(get_managed_cluster_names "$ctx")
        for cluster in $clusters; do
            if [[ -z "${seen_clusters[$cluster]:-}" ]]; then
                seen_clusters[$cluster]=1
                ALL_MANAGED_CLUSTERS+=("$cluster")
            fi
        done
    done
    
    # Check each managed cluster's klusterlet connection
    for mc in "${ALL_MANAGED_CLUSTERS[@]}"; do
        # Try to use the cluster name as context
        if ! test_context_reachable "$mc" 2>/dev/null; then
            echo -e "    ${YELLOW}⚠${NC} $mc: cannot reach cluster (skipped)"
            continue
        fi
        
        local klusterlet_server
        klusterlet_server=$(get_klusterlet_hub_server "$mc")
        
        if [[ -z "$klusterlet_server" ]]; then
            echo -e "    ${YELLOW}⚠${NC} $mc: no klusterlet config found"
            continue
        fi
        
        # Match klusterlet server to hub
        local matched=false
        for i in "${!HUB_CONTEXTS[@]}"; do
            local hub_server="${hub_servers[$i]}"
            # Compare by extracting hostname from both
            local hub_host klusterlet_host
            hub_host=$(echo "$hub_server" | sed 's|https://||' | cut -d: -f1)
            klusterlet_host=$(echo "$klusterlet_server" | sed 's|https://||' | cut -d: -f1)
            
            if [[ "$hub_host" == "$klusterlet_host" ]]; then
                klusterlet_counts[$i]=$((klusterlet_counts[$i] + 1))
                echo -e "    ${GREEN}✓${NC} $mc → ${HUB_CONTEXTS[$i]}"
                matched=true
                break
            fi
        done
        
        if [[ "$matched" == "false" ]]; then
            echo -e "    ${YELLOW}?${NC} $mc → $klusterlet_server (unknown hub)"
        fi
    done
    
    # Update the global array
    for i in "${!HUB_CONTEXTS[@]}"; do
        HUB_KLUSTERLET_COUNTS+=("${klusterlet_counts[$i]}")
    done
}

# Get detailed managed cluster status (for verbose mode)
# Returns formatted list of clusters with their status
get_cluster_details() {
    local ctx="$1"
    
    "$CLUSTER_CLI_BIN" --context="$ctx" get $RES_MANAGED_CLUSTER -o json 2>/dev/null | \
        jq -r --arg LOCAL "$LOCAL_CLUSTER_NAME" '
            .items[] | select(.metadata.name != $LOCAL) |
            {
                name: .metadata.name,
                available: ([.status.conditions[]? | select(.type=="ManagedClusterConditionAvailable")] | first | .status // "Unknown"),
                joined: ([.status.conditions[]? | select(.type=="ManagedClusterJoined")] | first | .status // "Unknown")
            } |
            "\(.name)|\(.available)|\(.joined)"
        ' 2>/dev/null || echo ""
}

# Get total managed cluster count (excluding local-cluster)
get_total_mc_count() {
    local ctx="$1"
    
    "$CLUSTER_CLI_BIN" --context="$ctx" get $RES_MANAGED_CLUSTER --no-headers 2>/dev/null | \
        grep -v "$LOCAL_CLUSTER_NAME" | wc -l
}

# Determine hub role based on collected information
# Sets HUB_ROLES and HUB_STATES arrays
determine_hub_role() {
    local ctx="$1"
    local backup_state="$2"
    local restore_state="$3"
    local available_mc="$4"
    local total_mc="$5"
    
    local role="unknown"
    local state=""
    
    # Decision logic
    if [[ "$backup_state" == "active" ]] && [[ "$restore_state" == "none" || "$restore_state" == "finished" ]]; then
        # Active BackupSchedule + no ongoing restore = Primary
        role="primary"
        state="Active primary hub (BackupSchedule running, $available_mc/$total_mc clusters available)"
    elif [[ "$backup_state" == "active" ]] && [[ "$restore_state" == "passive-sync" ]]; then
        # This is unusual - both backup and passive sync active
        role="unknown"
        state="WARNING: Both BackupSchedule and passive-sync active (check configuration)"
    elif [[ "$restore_state" == "passive-sync" ]]; then
        # Passive sync running = Secondary in standby mode
        role="secondary"
        state="Secondary hub in passive-sync mode (ready for switchover)"
    elif [[ "$backup_state" == "collision" ]]; then
        # BackupCollision = Recently restored, needs configuration
        role="standby"
        state="Standby hub with BackupCollision (needs BackupSchedule reconfiguration)"
    elif [[ "$backup_state" == "paused" ]] && [[ "$restore_state" == "finished" ]]; then
        # Paused backup + finished restore = Old primary after switchover
        role="old-primary"
        state="Old primary hub (BackupSchedule paused, restore finished)"
    elif [[ "$backup_state" == "paused" ]] && [[ "$restore_state" == "passive-sync" ]]; then
        # Paused backup + passive sync = Configured as secondary/failback
        role="secondary"
        state="Secondary hub (BackupSchedule paused, passive-sync enabled for failback)"
    elif [[ "$backup_state" == "paused" ]]; then
        role="standby"
        state="Standby hub (BackupSchedule paused)"
    elif [[ "$restore_state" == "finished" ]] && [[ "$backup_state" == "none" ]]; then
        # Restore finished but no BackupSchedule = Post-switchover, needs BackupSchedule
        role="new-primary"
        state="New primary hub (restore finished, BackupSchedule needs enabling)"
    elif [[ "$available_mc" -gt 0 ]] && [[ "$backup_state" == "none" ]]; then
        # Has connected clusters but no backup config
        role="primary"
        state="Likely primary hub ($available_mc clusters connected, no BackupSchedule)"
    else
        role="unknown"
        state="Unable to determine role (backup=$backup_state, restore=$restore_state)"
    fi
    
    echo "$role|$state"
}

# Analyze a single context
analyze_context() {
    local ctx="$1"
    
    echo -n "  Checking $ctx... "
    
    # Test connectivity
    if ! test_context_reachable "$ctx"; then
        echo -e "${YELLOW}unreachable (skipped)${NC}"
        return 1
    fi
    
    # Check if it's an ACM hub
    if ! is_acm_hub "$ctx"; then
        # Try to report OCP version and update channel even when ACM is not present
        local ocp_version ocp_channel
        ocp_version=$(get_ocp_version "$ctx")
        ocp_channel=$(get_ocp_channel "$ctx")
        echo -e "${GRAY}not an ACM hub (skipped)${NC} (OCP: ${ocp_version}, channel: ${ocp_channel})"
        return 1
    fi
    
    # Get ACM version
    local acm_version
    acm_version=$(get_acm_version "$ctx")
    echo -e "${GREEN}ACM hub detected${NC} (ACM ${BLUE}${acm_version}${NC})"

    # Get OCP version and update channel
    local ocp_version ocp_channel
    ocp_version=$(get_ocp_version "$ctx")
    ocp_channel=$(get_ocp_channel "$ctx")
    
    # Gather information
    local backup_state restore_state available_mc total_mc
    backup_state=$(get_backup_schedule_state "$ctx")
    restore_state=$(get_restore_state "$ctx")
    total_mc=$(get_total_mc_count "$ctx")
    available_mc=$(get_available_mc_count "$ctx")
    
    # Determine role
    local result
    result=$(determine_hub_role "$ctx" "$backup_state" "$restore_state" "$available_mc" "$total_mc")
    local role="${result%%|*}"
    local state="${result#*|}"
    
    # Store results
    HUB_CONTEXTS+=("$ctx")
    HUB_ROLES+=("$role")
    HUB_STATES+=("$state")
    HUB_MC_COUNTS+=("$available_mc/$total_mc")
    HUB_BACKUP_STATES+=("$backup_state")
    HUB_VERSIONS+=("$acm_version")
    HUB_OCP_VERSIONS+=("$ocp_version")
    HUB_OCP_CHANNELS+=("$ocp_channel")
    
    return 0
}

# Print discovered hubs in a nice format
print_discovered_hubs() {
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}Discovered ACM Hubs${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    
    for i in "${!HUB_CONTEXTS[@]}"; do
        local ctx="${HUB_CONTEXTS[$i]}"
        local role="${HUB_ROLES[$i]}"
        local state="${HUB_STATES[$i]}"
        local mc_count="${HUB_MC_COUNTS[$i]}"
        local klusterlet_count="${HUB_KLUSTERLET_COUNTS[$i]:-}"
        local version="${HUB_VERSIONS[$i]:-unknown}"
        
        # Color based on role
        local role_color="$NC"
        case "$role" in
            primary|new-primary)
                role_color="$GREEN"
                ;;
            secondary)
                role_color="$BLUE"
                ;;
            standby|old-primary)
                role_color="$YELLOW"
                ;;
            unknown)
                role_color="$RED"
                ;;
        esac
        
        echo -e "  ${role_color}●${NC} ${BLUE}$ctx${NC}"
        echo -e "    Role:     ${role_color}$role${NC}"
        local ocp_version="${HUB_OCP_VERSIONS[$i]:-unknown}"
        local ocp_channel="${HUB_OCP_CHANNELS[$i]:-n/a}"
        echo -e "    ACM:      $version"
        echo -e "    OCP:      ${ocp_version} (channel: ${ocp_channel})"
        
        # Show cluster counts - include klusterlet count if we verified
        if [[ -n "$klusterlet_count" ]] && [[ "${#HUB_KLUSTERLET_COUNTS[@]}" -gt 0 ]]; then
            local available="${mc_count%%/*}"
            local total="${mc_count#*/}"
            if [[ "$klusterlet_count" != "$available" ]]; then
                # Klusterlet count differs from reported available - show both
                echo -e "    Clusters: ${mc_count} (reported), ${GREEN}${klusterlet_count}${NC} (actual klusterlet connections)"
            else
                echo -e "    Clusters: $mc_count"
            fi
        else
            echo -e "    Clusters: $mc_count"
        fi
        echo -e "    State:    $state"
        
        # Show detailed cluster info in verbose mode
        if [[ "$VERBOSE" == "true" ]]; then
            local cluster_details
            cluster_details=$(get_cluster_details "$ctx")
            if [[ -n "$cluster_details" ]]; then
                echo -e "    Cluster Details:"
                while IFS='|' read -r name available joined; do
                    local status_color="$GREEN"
                    local status_icon="✓"
                    if [[ "$available" != "True" ]]; then
                        status_color="$RED"
                        status_icon="✗"
                    fi
                    echo -e "      ${status_color}${status_icon}${NC} $name (Available=$available, Joined=$joined)"
                done <<< "$cluster_details"
            fi
        fi
        echo ""
    done
}

# Propose the appropriate check command
propose_check() {
    local primary_ctx=""
    local secondary_ctx=""
    local new_hub_ctx=""
    local old_hub_ctx=""
    local proposal_type=""
    declare -a proposal_cmd=()
    
    # Find hubs by role
    for i in "${!HUB_CONTEXTS[@]}"; do
        local ctx="${HUB_CONTEXTS[$i]}"
        local role="${HUB_ROLES[$i]}"
        
        case "$role" in
            primary)
                primary_ctx="$ctx"
                ;;
            secondary)
                secondary_ctx="$ctx"
                ;;
            new-primary)
                new_hub_ctx="$ctx"
                ;;
            old-primary|standby)
                if [[ -z "$old_hub_ctx" ]]; then
                    old_hub_ctx="$ctx"
                fi
                ;;
        esac
    done
    
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}Recommended Action${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    
    # Determine the scenario
    if [[ -n "$primary_ctx" ]] && [[ -n "$secondary_ctx" ]]; then
        # Pre-switchover scenario: Primary + Secondary with passive-sync
        proposal_type="preflight"
        proposal_cmd=("${SCRIPT_DIR}/preflight-check.sh" "--primary-context" "$primary_ctx" "--secondary-context" "$secondary_ctx" "--method" "passive")
        
        echo -e "  ${GREEN}Scenario: Pre-Switchover${NC}"
        echo "  Primary hub ($primary_ctx) and secondary hub ($secondary_ctx) detected."
        echo "  Run preflight checks before initiating switchover."
        echo ""
        
    elif [[ -n "$new_hub_ctx" ]] && [[ -n "$old_hub_ctx" ]]; then
        # Post-switchover scenario: New primary + Old primary
        proposal_type="postflight"
        proposal_cmd=("${SCRIPT_DIR}/postflight-check.sh" "--new-hub-context" "$new_hub_ctx" "--old-hub-context" "$old_hub_ctx")
        
        echo -e "  ${GREEN}Scenario: Post-Switchover${NC}"
        echo "  New primary ($new_hub_ctx) and old primary ($old_hub_ctx) detected."
        echo "  Run postflight checks to verify switchover completion."
        echo ""
        
    elif [[ -n "$new_hub_ctx" ]]; then
        # Post-switchover without old hub comparison
        proposal_type="postflight"
        proposal_cmd=("${SCRIPT_DIR}/postflight-check.sh" "--new-hub-context" "$new_hub_ctx")
        
        echo -e "  ${GREEN}Scenario: Post-Switchover (single hub)${NC}"
        echo "  New primary ($new_hub_ctx) detected."
        echo "  Run postflight checks to verify switchover completion."
        echo ""
        
    elif [[ -n "$primary_ctx" ]] && [[ -z "$secondary_ctx" ]]; then
        # Only primary hub found
        echo -e "  ${YELLOW}Scenario: Single Primary Hub${NC}"
        echo "  Only primary hub ($primary_ctx) found."
        echo "  No secondary hub detected with passive-sync enabled."
        echo ""
        echo "  To prepare for switchover:"
        echo "    1. Ensure secondary hub has OADP/Velero configured"
        echo "    2. Create a Restore resource with syncRestoreWithNewBackups=true"
        echo ""
        return 1
        
    elif [[ ${#HUB_CONTEXTS[@]} -eq 0 ]]; then
        echo -e "  ${RED}No ACM hubs found${NC}"
        echo "  Could not find any reachable Kubernetes contexts with ACM installed."
        echo ""
        return 1
        
    else
        echo -e "  ${YELLOW}Unable to determine switchover state${NC}"
        echo "  Found ${#HUB_CONTEXTS[@]} hub(s) but could not determine clear primary/secondary roles."
        echo "  Review the hub states above and specify contexts manually."
        echo ""
        echo "  For preflight checks:"
        echo "    ${SCRIPT_DIR}/preflight-check.sh --primary-context <primary> --secondary-context <secondary> --method passive"
        echo ""
        echo "  For postflight checks:"
        echo "    ${SCRIPT_DIR}/postflight-check.sh --new-hub-context <new> [--old-hub-context <old>]"
        echo ""
        return 1
    fi
    
    # Print the proposed command
    echo -e "  ${GREEN}Proposed command:${NC}"
    echo -e "    ${proposal_cmd[*]}"
    echo ""
    
    # Execute if --run was specified
    if [[ "$RUN_PROPOSED" == "true" ]]; then
        echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${BLUE}Executing $proposal_type checks...${NC}"
        echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo ""
        exec "${proposal_cmd[@]}"
    fi
    
    return 0
}

# =============================================================================
# Main
# =============================================================================

# Parse arguments
# Show help if no arguments provided
if [[ $# -eq 0 ]]; then
    usage
fi

while [[ $# -gt 0 ]]; do
    case $1 in
        --auto)
            AUTO_DISCOVER=true
            shift
            ;;
        --contexts)
            CONTEXTS="$2"
            shift 2
            ;;
        --run)
            RUN_PROPOSED=true
            shift
            ;;
        --verbose|-v)
            VERBOSE=true
            shift
            ;;
        --timeout)
            CONNECTION_TIMEOUT="$2"
            shift 2
            ;;
        --help|-h)
            usage
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit "$EXIT_INVALID_ARGS"
            ;;
    esac
done

# Header
echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║   ACM Hub Discovery                                        ║"
echo "╚════════════════════════════════════════════════════════════╝"
print_script_version "discover-hub.sh"
echo ""

# Check CLI tools (using function from lib-common.sh)
section_header "Checking Prerequisites"
detect_cluster_cli

if [[ -z "$CLUSTER_CLI_BIN" ]]; then
    echo -e "${RED}Error: No Kubernetes CLI found. Install oc or kubectl.${NC}"
    exit "$EXIT_FAILURE"
fi

# Check for jq (required for this script)
if ! command -v jq &>/dev/null; then
    echo -e "${RED}Error: jq is required for this script but not found.${NC}"
    exit "$EXIT_FAILURE"
fi

# Get list of contexts to check
section_header "Discovering Kubernetes Contexts"

declare -a CONTEXT_LIST=()

if [[ -n "$CONTEXTS" ]]; then
    # Use provided contexts
    IFS=',' read -ra CONTEXT_LIST <<< "$CONTEXTS"
    echo "Using specified contexts: ${CONTEXT_LIST[*]}"
elif [[ "$AUTO_DISCOVER" == "true" ]]; then
    # Auto-discover from kubeconfig
    while IFS= read -r ctx; do
        [[ -n "$ctx" ]] && CONTEXT_LIST+=("$ctx")
    done < <("$CLUSTER_CLI_BIN" config get-contexts -o name 2>/dev/null)
    
    if [[ ${#CONTEXT_LIST[@]} -eq 0 ]]; then
        echo -e "${RED}No Kubernetes contexts found in kubeconfig${NC}"
        exit "$EXIT_FAILURE"
    fi
    
    echo "Found ${#CONTEXT_LIST[@]} context(s) in kubeconfig"
else
    echo -e "${RED}Error: Either --auto or --contexts must be specified${NC}"
    echo "Use --help for usage information"
    exit "$EXIT_INVALID_ARGS"
fi

# Analyze each context
section_header "Analyzing Contexts"

for ctx in "${CONTEXT_LIST[@]}"; do
    analyze_context "$ctx" || true
done

# Check if we found any hubs
if [[ ${#HUB_CONTEXTS[@]} -eq 0 ]]; then
    echo ""
    echo -e "${RED}No ACM hubs found among the checked contexts.${NC}"
    echo "Ensure your kubeconfig contains contexts for ACM hub clusters."
    exit "$EXIT_FAILURE"
fi

# Verify klusterlet connections if needed
verify_klusterlet_connections

# Print discovered hubs
print_discovered_hubs

# Propose the appropriate check
if propose_check; then
    exit "$EXIT_SUCCESS"
else
    exit "$EXIT_FAILURE"
fi
