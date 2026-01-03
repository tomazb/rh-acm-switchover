#!/bin/bash
# Enhanced Monitoring Script for ACM Switchover Phases
# Provides real-time monitoring and alerting for critical resources
# Usage: ./phase_monitor.sh [--primary <ctx>] [--secondary <ctx>] [--phase <phase>] [--output-dir <dir>]
#
# DEPRECATION NOTICE:
#   This bash script is deprecated in favor of Python-based monitoring.
#   The Python E2E orchestrator includes integrated monitoring.
#   For CI/automated testing, use: pytest -m e2e tests/e2e/
#   This script will be removed in a future release.

set -euo pipefail

# Source constants and utilities
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/../../scripts/constants.sh" ]]; then
    source "${SCRIPT_DIR}/../../scripts/constants.sh"
else
    echo "ERROR: Cannot find scripts/constants.sh" >&2
    exit 1
fi

# log_error fallback if lib-common.sh is not sourced
if ! type log_error &>/dev/null; then
    log_error() {
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] $*" >&2
    }
fi

# Configuration
PRIMARY_CONTEXT=${PRIMARY_CONTEXT:-mgmt1}
SECONDARY_CONTEXT=${SECONDARY_CONTEXT:-mgmt2}
PHASE=${PHASE:-switchover}
OUTPUT_DIR=${OUTPUT_DIR:-./monitoring-$(date +%Y%m%d-%H%M%S)}
INTERVAL=${INTERVAL:-30}
ALERT_THRESHOLDS=${ALERT_THRESHOLDS:-true}

# Note: ACM_NAMESPACE, BACKUP_NAMESPACE, OBSERVABILITY_NAMESPACE, LOCAL_CLUSTER_NAME
# are now provided by scripts/constants.sh

# Alert thresholds
readonly CLUSTER_UNAVAILABLE_THRESHOLD=300  # 5 minutes
readonly BACKUP_FAILURE_THRESHOLD=600      # 10 minutes
readonly RESTORE_STALLED_THRESHOLD=900      # 15 minutes

# State tracking
declare -A LAST_SEEN_STATES=()
declare -A ALERT_COUNTS=()
MONITOR_START_TIME=""

# =============================================================================
# Utility Functions
# =============================================================================

log_message() {
    local level=${1:-INFO}
    local message=$2
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$level] $message"
}

setup_monitoring() {
    mkdir -p "$OUTPUT_DIR"
    mkdir -p "$OUTPUT_DIR/alerts"
    mkdir -p "$OUTPUT_DIR/metrics"
    mkdir -p "$OUTPUT_DIR/logs"
    
    MONITOR_START_TIME=$(date +%s)
    
    log_message "INFO" "Starting phase monitoring for: $PHASE"
    log_message "INFO" "Primary: $PRIMARY_CONTEXT, Secondary: $SECONDARY_CONTEXT"
    log_message "INFO" "Output directory: $OUTPUT_DIR"
    log_message "INFO" "Monitoring interval: ${INTERVAL}s"
}

# =============================================================================
# Resource Monitoring Functions
# =============================================================================

monitor_managed_clusters() {
    local context=$1
    local hub_type=$2
    local timestamp=$(date +%s)
    local log_file="${OUTPUT_DIR}/logs/${hub_type}_managed_clusters.log"
    
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$log_file"
    
    # Get all managed clusters
    local clusters_json
    clusters_json=$(kubectl --context "$context" get managedclusters -o json 2>/dev/null || echo '{"items": []}')
    
    # Process each cluster using process substitution to avoid subshell variable mutation
    while IFS='|' read -r name available joined accepted; do
        if [[ -n "$name" ]]; then
            echo "Cluster: $name, Available: $available, Joined: $joined, Accepted: $accepted" >> "$log_file"
            
            # Check for alerts
            if [[ "$ALERT_THRESHOLDS" == "true" ]]; then
                check_cluster_alerts "$hub_type" "$name" "$available" "$timestamp"
            fi
        fi
    done < <(echo "$clusters_json" | jq -r --arg LOCAL "$LOCAL_CLUSTER_NAME" '
        .items[] | 
        select(.metadata.name != $LOCAL) |
        {
            name: .metadata.name,
            available: ([.status.conditions[]? | select(.type=="ManagedClusterConditionAvailable")] | first | .status // "Unknown"),
            joined: ([.status.conditions[]? | select(.type=="ManagedClusterJoined")] | first | .status // "Unknown"),
            accepted: ([.status.conditions[]? | select(.type=="ManagedClusterAccepted")] | first | .status // "Unknown")
        } |
        "\(.name)|\(.available)|\(.joined)|\(.accepted)"
    ' 2>/dev/null)
    
    echo "" >> "$log_file"
}

check_cluster_alerts() {
    local hub_type=$1
    local cluster_name=$2
    local available=$3
    local timestamp=$4
    
    local state_key="${hub_type}_${cluster_name}"
    local last_seen="${LAST_SEEN_STATES[$state_key]:-}"
    
    if [[ "$available" != "True" ]]; then
        if [[ -n "$last_seen" ]]; then
            local duration=$((timestamp - last_seen))
            if [[ $duration -gt $CLUSTER_UNAVAILABLE_THRESHOLD ]]; then
                local alert_key="${state_key}_unavailable"
                ((ALERT_COUNTS[$alert_key]++))
                
                if [[ ${ALERT_COUNTS[$alert_key]} -eq 1 ]]; then
                    generate_alert "CLUSTER_UNAVAILABLE" "$hub_type" "$cluster_name" "Cluster unavailable for ${duration}s"
                fi
            fi
        else
            LAST_SEEN_STATES[$state_key]=$timestamp
        fi
    else
        # Reset alert state when cluster becomes available
        unset LAST_SEEN_STATES[$state_key]
        local alert_key="${state_key}_unavailable"
        unset ALERT_COUNTS[$alert_key]
    fi
}

monitor_backup_restore() {
    local context=$1
    local hub_type=$2
    local timestamp=$(date +%s)
    local log_file="${OUTPUT_DIR}/logs/${hub_type}_backup_restore.log"
    
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$log_file"
    
    # Monitor backup schedules (primary hub)
    if [[ "$hub_type" == "primary" ]]; then
        local backup_schedule
        backup_schedule=$(kubectl --context "$context" get backupschedules -n "$BACKUP_NAMESPACE" -o json 2>/dev/null || echo '{"items": []}')
        
        echo "$backup_schedule" | jq -r '
            .items[] |
            {
                name: .metadata.name,
                phase: .status.phase // "Unknown",
                paused: .spec.paused // false,
                lastBackup: .status.lastBackupTime // "Never"
            } |
            "BackupSchedule: \(.name), Phase: \(.phase), Paused: \(.paused), LastBackup: \(.lastBackup)"
        ' 2>/dev/null >> "$log_file" || true
        
        # Check backup alerts
        if [[ "$ALERT_THRESHOLDS" == "true" ]]; then
            check_backup_alerts "$hub_type" "$backup_schedule" "$timestamp"
        fi
    fi
    
    # Monitor restores (secondary hub)
    if [[ "$hub_type" == "secondary" ]]; then
        local restores
        restores=$(kubectl --context "$context" get restores -n "$BACKUP_NAMESPACE" -o json 2>/dev/null || echo '{"items": []}')
        
        echo "$restores" | jq -r '
            .items[] |
            {
                name: .metadata.name,
                phase: .status.phase // "Unknown",
                started: .status.startTimestamp // "Unknown",
                completed: .status.completionTimestamp // "Running"
            } |
            "Restore: \(.name), Phase: \(.phase), Started: \(.started), Completed: \(.completed)"
        ' 2>/dev/null >> "$log_file" || true
        
        # Check restore alerts
        if [[ "$ALERT_THRESHOLDS" == "true" ]]; then
            check_restore_alerts "$hub_type" "$restores" "$timestamp"
        fi
    fi
    
    echo "" >> "$log_file"
}

check_backup_alerts() {
    local hub_type=$1
    local backup_schedule=$2
    local timestamp=$3
    
    local phase
    phase=$(echo "$backup_schedule" | jq -r '.items[0].status.phase // "Unknown"' 2>/dev/null || echo "Unknown")
    
    if [[ "$phase" == "Failed" || "$phase" == "PartiallyFailed" ]]; then
        local failure_state_key="${hub_type}_backup_failure_started"
        local failure_start=${LAST_SEEN_STATES[$failure_state_key]:-}

        if [[ -z "$failure_start" ]]; then
            LAST_SEEN_STATES[$failure_state_key]=$timestamp
            failure_start=$timestamp
        fi

        local duration=$((timestamp - failure_start))

        if (( duration >= BACKUP_FAILURE_THRESHOLD )); then
            local alert_key="${hub_type}_backup_failure_exceeded"
            ((ALERT_COUNTS[$alert_key]++))

            if [[ ${ALERT_COUNTS[$alert_key]} -eq 1 ]]; then
                generate_alert "BACKUP_FAILURE" "$hub_type" "BackupSchedule" "Backup failing for ${duration}s (phase: $phase)"
            fi
        fi
    else
        local failure_state_key="${hub_type}_backup_failure_started"
        unset LAST_SEEN_STATES[$failure_state_key]
        local alert_key="${hub_type}_backup_failure_exceeded"
        unset ALERT_COUNTS[$alert_key]
    fi
}

check_restore_alerts() {
    local hub_type=$1
    local restores=$2
    local timestamp=$3
    
    local restore_count
    restore_count=$(echo "$restores" | jq '.items | length' 2>/dev/null || echo "0")
    
    if [[ $restore_count -gt 0 ]]; then
        local latest_restore
        latest_restore=$(echo "$restores" | jq -r '.items[-1]' 2>/dev/null || echo "{}")
        
        local phase
        phase=$(echo "$latest_restore" | jq -r '.status.phase // "Unknown"' 2>/dev/null || echo "Unknown")
        
        local start_time_str
        start_time_str=$(echo "$latest_restore" | jq -r '.status.startTimestamp // ""' 2>/dev/null || echo "")
        
        if [[ -n "$start_time_str" && "$phase" != "Completed" && "$phase" != "Failed" ]]; then
            # Check if restore is stalled
            local start_time
            start_time=$(date -d "$start_time_str" +%s 2>/dev/null || echo "$timestamp")
            local duration=$((timestamp - start_time))
            
            if [[ $duration -gt $RESTORE_STALLED_THRESHOLD ]]; then
                local alert_key="${hub_type}_restore_stalled"
                ((ALERT_COUNTS[$alert_key]++))
                
                if [[ ${ALERT_COUNTS[$alert_key]} -eq 1 ]]; then
                    generate_alert "RESTORE_STALLED" "$hub_type" "Restore" "Restore stalled in phase '$phase' for ${duration}s"
                fi
            fi
        fi
    fi
}

monitor_observability() {
    local context=$1
    local hub_type=$2
    local log_file="${OUTPUT_DIR}/logs/${hub_type}_observability.log"
    
    # Skip if observability namespace doesn't exist
    if ! kubectl --context "$context" get namespace "$OBSERVABILITY_NAMESPACE" &>/dev/null; then
        return
    fi
    
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$log_file"
    
    # Monitor key observability deployments
    local deployments="observability-thanos-compact observability-observatorium-api observability-thanos-receive"
    
    for deployment in $deployments; do
        local resource_type="deployment"
        if [[ "$deployment" == *"thanos-compact"* ]]; then
            resource_type="statefulset"
        fi
        
        local status
        status=$(kubectl --context "$context" get "$resource_type" "$deployment" -n "$OBSERVABILITY_NAMESPACE" -o custom-columns=DESIRED:.spec.replicas,READY:.status.readyReplicas --no-headers 2>/dev/null || echo "0 0")
        
        local desired ready
        desired=$(echo "$status" | awk '{print $1}')
        ready=$(echo "$status" | awk '{print $2}')
        
        echo "$deployment ($resource_type): $desired/$ready replicas" >> "$log_file"
        
        # Alert when pods are not all ready (avoid implying scale-up intent)
        if [[ "$desired" != "0" && "$desired" != "$ready" ]]; then
            generate_alert "OBSERVABILITY_NOT_READY" "$hub_type" "$deployment" "Pods not ready: desired=$desired ready=$ready"
        fi
    done
    
    echo "" >> "$log_file"
}

monitor_klusterlets() {
    local context=$1
    local hub_type=$2
    local log_file="${OUTPUT_DIR}/logs/${hub_type}_klusterlets.log"
    
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$log_file"
    
    # Get all managed clusters and check their klusterlet status
    local clusters
    clusters=$(kubectl --context "$context" get managedclusters -o jsonpath='{.items[*].metadata.name}' 2>/dev/null || echo "")
    
    for cluster in $clusters; do
        if [[ "$cluster" != "$LOCAL_CLUSTER_NAME" ]]; then
            # Try to get klusterlet status from the managed cluster itself
            # This requires the managed cluster context to be available
            local klusterlet_status="Unknown"
            
            if kubectl config get-contexts "$cluster" &>/dev/null; then
                klusterlet_status=$(kubectl --context "$cluster" get klusterlet -n open-cluster-management-agent -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "Unknown")
            fi
            
            echo "Klusterlet for $cluster: $klusterlet_status" >> "$log_file"
        fi
    done
    
    echo "" >> "$log_file"
}

# =============================================================================
# Alert Generation Functions
# =============================================================================

generate_alert() {
    local alert_type=$1
    local hub_type=$2
    local resource=$3
    local message=$4
    local timestamp=$(date -Iseconds)
    local alert_file="${OUTPUT_DIR}/alerts/${alert_type}_${hub_type}_${resource}.json"
    
    {
        echo "{"
        echo "  \"alert_type\": \"$alert_type\","
        echo "  \"hub_type\": \"$hub_type\","
        echo "  \"resource\": \"$resource\","
        echo "  \"message\": \"$message\","
        echo "  \"timestamp\": \"$timestamp\","
        echo "  \"phase\": \"$PHASE\""
        echo "}"
    } > "$alert_file"
    
    log_message "ALERT" "$alert_type on $hub_type: $resource - $message"
}

# =============================================================================
# Metrics Collection Functions
# =============================================================================

collect_metrics() {
    local timestamp=$(date +%s)
    # Use consistent metrics_*.json naming pattern (not cycle*_*.json)
    local metrics_file="${OUTPUT_DIR}/metrics/metrics_${timestamp}.json"

    local alert_types_json="[]"
    if [[ ${#ALERT_COUNTS[@]} -gt 0 ]]; then
        local sorted_keys
        sorted_keys=$(printf '%s\n' "${!ALERT_COUNTS[@]}" | sort -u)

        local json_items=()
        local key
        while IFS= read -r key; do
            [[ -z "$key" ]] && continue

            local escaped_key="$key"
            escaped_key=${escaped_key//\\/\\\\}
            escaped_key=${escaped_key//\"/\\\"}

            json_items+=("\"${escaped_key}\"")
        done <<< "$sorted_keys"

        local IFS=,
        alert_types_json="[${json_items[*]}]"
    fi
    
    {
        echo "{"
        echo "  \"timestamp\": $(date +%s),"
        echo "  \"iso_timestamp\": \"$(date -Iseconds)\","
        echo "  \"phase\": \"$PHASE\","
        echo "  \"monitoring_duration\": $((timestamp - MONITOR_START_TIME)),"
        
        # Primary hub metrics
        echo "  \"primary\": {"
        collect_hub_metrics "$PRIMARY_CONTEXT" "primary"
        echo "  },"
        
        # Secondary hub metrics  
        echo "  \"secondary\": {"
        collect_hub_metrics "$SECONDARY_CONTEXT" "secondary"
        echo "  },"
        
        # Alert summary
        echo "  \"alerts\": {"
        echo "    \"total_alerts\": $((${#ALERT_COUNTS[@]})),"
        echo "    \"alert_types\": ${alert_types_json}"
        echo "  }"
        echo "}"
    } > "$metrics_file"
}

collect_hub_metrics() {
    local context=$1
    local hub_type=$2
    
    # Managed cluster metrics
    local total_mc=$(kubectl --context "$context" get managedclusters --no-headers 2>/dev/null | grep -v "$LOCAL_CLUSTER_NAME" | wc -l || echo "0")
    local available_mc=$(kubectl --context "$context" get managedclusters -o json 2>/dev/null | jq -r --arg LOCAL "$LOCAL_CLUSTER_NAME" '[.items[] | select(.metadata.name != $LOCAL) | select(.status.conditions[]? | select(.type=="ManagedClusterConditionAvailable" and .status=="True"))] | length' 2>/dev/null || echo "0")
    
    echo "    \"total_managed_clusters\": $total_mc,"
    echo "    \"available_managed_clusters\": $available_mc,"
    
    # Backup/restore metrics
    if [[ "$hub_type" == "primary" ]]; then
        local backup_phase=$(kubectl --context "$context" get backupschedules -n "$BACKUP_NAMESPACE" -o jsonpath='{.items[0].status.phase}' 2>/dev/null || echo "none")
        echo "    \"backup_phase\": \"$backup_phase\","
    else
        local restore_count=$(kubectl --context "$context" get restores -n "$BACKUP_NAMESPACE" --no-headers 2>/dev/null | wc -l || echo "0")
        local latest_restore_phase=$(kubectl --context "$context" get restores -n "$BACKUP_NAMESPACE" --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].status.phase}' 2>/dev/null || echo "none")
        echo "    \"restore_count\": $restore_count,"
        echo "    \"latest_restore_phase\": \"$latest_restore_phase\","
    fi
    
    # Observability metrics
    if kubectl --context "$context" get namespace "$OBSERVABILITY_NAMESPACE" &>/dev/null; then
        local obs_deployments=$(kubectl --context "$context" get deployments -n "$OBSERVABILITY_NAMESPACE" --no-headers 2>/dev/null | wc -l || echo "0")
        local obs_statefulsets=$(kubectl --context "$context" get statefulsets -n "$OBSERVABILITY_NAMESPACE" --no-headers 2>/dev/null | wc -l || echo "0")
        echo "    \"observability_deployments\": $obs_deployments,"
        echo "    \"observability_statefulsets\": $obs_statefulsets"
    else
        echo "    \"observability_deployments\": 0,"
        echo "    \"observability_statefulsets\": 0"
    fi
}

# =============================================================================
# Main Monitoring Loop
# =============================================================================

monitoring_loop() {
    log_message "INFO" "Starting monitoring loop (interval: ${INTERVAL}s)"
    
    while true; do
        local timestamp=$(date +%s)
        
        # Monitor primary hub
        monitor_managed_clusters "$PRIMARY_CONTEXT" "primary"
        monitor_backup_restore "$PRIMARY_CONTEXT" "primary"
        monitor_observability "$PRIMARY_CONTEXT" "primary"
        monitor_klusterlets "$PRIMARY_CONTEXT" "primary"
        
        # Monitor secondary hub
        monitor_managed_clusters "$SECONDARY_CONTEXT" "secondary"
        monitor_backup_restore "$SECONDARY_CONTEXT" "secondary"
        monitor_observability "$SECONDARY_CONTEXT" "secondary"
        monitor_klusterlets "$SECONDARY_CONTEXT" "secondary"
        
        # Collect comprehensive metrics
        collect_metrics
        
        # Clean up old metrics files (keep last 50)
        find "${OUTPUT_DIR}/metrics" -name "metrics_*.json" -type f | sort -r | tail -n +51 | xargs rm -f 2>/dev/null || true
        
        sleep "$INTERVAL"
    done
}

# =============================================================================
# Signal Handling and Cleanup
# =============================================================================

cleanup() {
    log_message "INFO" "Monitoring stopped by user"
    log_message "INFO" "Results saved to: $OUTPUT_DIR"
    
    # Generate final summary
    local summary_file="${OUTPUT_DIR}/monitoring_summary.txt"
    {
        echo "Phase Monitoring Summary"
        echo "======================="
        echo "Phase: $PHASE"
        echo "Started: $(date -d "@$MONITOR_START_TIME" '+%Y-%m-%d %H:%M:%S')"
        echo "Stopped: $(date '+%Y-%m-%d %H:%M:%S')"
        echo "Duration: $(($(date +%s) - MONITOR_START_TIME)) seconds"
        echo ""
        echo "Alerts Generated: ${#ALERT_COUNTS[@]}"
        for alert_key in "${!ALERT_COUNTS[@]}"; do
            echo "  $alert_key: ${ALERT_COUNTS[$alert_key]}"
        done
        echo ""
        echo "Files Generated:"
        echo "  Logs: ${OUTPUT_DIR}/logs/"
        echo "  Metrics: ${OUTPUT_DIR}/metrics/"
        echo "  Alerts: ${OUTPUT_DIR}/alerts/"
    } > "$summary_file"
    
    exit 0
}

# =============================================================================
# Deprecation Warning
# =============================================================================

print_deprecation_warning() {
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════════════════╗"
    echo "║  DEPRECATION WARNING                                                       ║"
    echo "║                                                                             ║"
    echo "║  This bash script is deprecated in favor of Python-based monitoring.       ║"
    echo "║  The Python E2E orchestrator includes integrated monitoring.               ║"
    echo "║  For CI/automated testing, use: pytest -m e2e tests/e2e/                   ║"
    echo "║                                                                             ║"
    echo "║  This script will be removed in a future release.                          ║"
    echo "╚═══════════════════════════════════════════════════════════════════════════╝"
    echo ""
    sleep 2
}

# =============================================================================
# Main Entry Point
# =============================================================================

main() {
    # Print deprecation warning
    print_deprecation_warning
    
    # Parse command line arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --primary)
                PRIMARY_CONTEXT="$2"
                shift 2
                ;;
            --secondary)
                SECONDARY_CONTEXT="$2"
                shift 2
                ;;
            --phase)
                PHASE="$2"
                shift 2
                ;;
            --output-dir)
                OUTPUT_DIR="$2"
                shift 2
                ;;
            --interval)
                INTERVAL="$2"
                shift 2
                ;;
            --no-alerts)
                ALERT_THRESHOLDS=false
                shift
                ;;
            -h|--help)
                echo "Usage: $0 [--primary <ctx>] [--secondary <ctx>] [--phase <phase>] [--output-dir <dir>] [--interval <seconds>] [--no-alerts]"
                exit 0
                ;;
            *)
                log_message "ERROR" "Unknown option: $1"
                exit 1
                ;;
        esac
    done
    
    # Setup monitoring
    setup_monitoring
    
    # Set up signal handlers
    trap cleanup INT TERM
    
    # Start monitoring loop
    monitoring_loop
}

# Execute main function
main "$@"
