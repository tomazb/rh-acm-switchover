#!/bin/bash
# ACM Switchover E2E Test Orchestrator
# Runs 5 consecutive switchover cycles with comprehensive monitoring
# Usage: ./e2e_test_orchestrator.sh [--primary <ctx>] [--secondary <ctx>] [--cycles <n>]
#
# ┌──────────────────────────────────────────────────────────────────────┐
# │ ⚠️  DEPRECATION WARNING - THIS SCRIPT WILL BE REMOVED               │
# ├──────────────────────────────────────────────────────────────────────┤
# │ This bash script is DEPRECATED and will be removed in version 2.0   │
# │                                                                       │
# │ MIGRATE TO: pytest -m e2e tests/e2e/                                │
# │                                                                       │
# │ See: tests/e2e/MIGRATION.md for detailed migration guide            │
# └──────────────────────────────────────────────────────────────────────┘

set -euo pipefail

# Source constants and utilities
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/../../scripts/constants.sh" ]]; then
    source "${SCRIPT_DIR}/../../scripts/constants.sh"
else
    echo "ERROR: Cannot find scripts/constants.sh" >&2
    exit 1
fi
if [[ -f "${SCRIPT_DIR}/../../scripts/lib-common.sh" ]]; then
    source "${SCRIPT_DIR}/../../scripts/lib-common.sh"
fi

# log_error fallback if lib-common.sh is not sourced or doesn't define it
if ! type log_error &>/dev/null; then
    log_error() {
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] $*" >&2
    }
fi

# Configuration
PRIMARY_CONTEXT=${PRIMARY_CONTEXT:-mgmt1}
SECONDARY_CONTEXT=${SECONDARY_CONTEXT:-mgmt2}
CYCLES=${CYCLES:-5}
REPORT_DIR="./e2e-results-$(date +%Y%m%d-%H%M%S)"
LOG_FILE="${REPORT_DIR}/e2e-orchestrator.log"
MONITORING_INTERVAL=30

# Test phases
declare -a PHASES=(
    "preflight"
    "primary_prep" 
    "activation"
    "post_activation"
    "finalization"
)

# Global state
declare -A CYCLE_RESULTS=()
declare -A PHASE_TIMINGS=()
TOTAL_START_TIME=""

# =============================================================================
# Deprecation Warning
# =============================================================================

print_deprecation_warning() {
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════════════════╗"
    echo "║  DEPRECATION WARNING                                                       ║"
    echo "║                                                                             ║"
    echo "║  This bash script is deprecated in favor of the Python E2E orchestrator.   ║"
    echo "║  For CI/automated testing, use: pytest -m e2e tests/e2e/                   ║"
    echo "║  For programmatic usage: from tests.e2e.orchestrator import E2EOrchestrator║"
    echo "║                                                                             ║"
    echo "║  This script will be removed in a future release.                          ║"
    echo "╚═══════════════════════════════════════════════════════════════════════════╝"
    echo ""
    sleep 2
}

# =============================================================================
# Utility Functions
# =============================================================================

log_message() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

setup_environment() {
    log_message "Setting up E2E test environment..."
    
    # Create report directory
    mkdir -p "$REPORT_DIR"
    mkdir -p "$REPORT_DIR/logs"
    mkdir -p "$REPORT_DIR/states"
    mkdir -p "$REPORT_DIR/metrics"
    
    # Initialize results tracking
    echo "cycle,phase,status,start_time,end_time,duration_seconds,exit_code" > "${REPORT_DIR}/cycle_results.csv"
    
    log_message "Report directory: $REPORT_DIR"
    log_message "Configuration: Primary=$PRIMARY_CONTEXT, Secondary=$SECONDARY_CONTEXT, Cycles=$CYCLES"
}

validate_environment() {
    log_message "Validating environment readiness..."
    
    # Check contexts exist
    if ! kubectl config get-contexts "$PRIMARY_CONTEXT" &>/dev/null; then
        log_error "Primary context '$PRIMARY_CONTEXT' not found"
        return 1
    fi
    
    if ! kubectl config get-contexts "$SECONDARY_CONTEXT" &>/dev/null; then
        log_error "Secondary context '$SECONDARY_CONTEXT' not found"
        return 1
    fi
    
    # Check ACM installation
    if ! kubectl --context "$PRIMARY_CONTEXT" get namespace "$ACM_NAMESPACE" &>/dev/null; then
        log_error "ACM not installed on primary hub"
        return 1
    fi
    
    if ! kubectl --context "$SECONDARY_CONTEXT" get namespace "$ACM_NAMESPACE" &>/dev/null; then
        log_error "ACM not installed on secondary hub"
        return 1
    fi
    
    # Check OADP installation
    if ! kubectl --context "$PRIMARY_CONTEXT" get namespace "$BACKUP_NAMESPACE" &>/dev/null; then
        log_error "OADP not installed on primary hub"
        return 1
    fi
    
    if ! kubectl --context "$SECONDARY_CONTEXT" get namespace "$BACKUP_NAMESPACE" &>/dev/null; then
        log_error "OADP not installed on secondary hub"
        return 1
    fi
    
    log_message "✅ Environment validation passed"
    return 0
}

# =============================================================================
# Monitoring Functions
# =============================================================================

start_phase_monitoring() {
    local cycle=$1
    local phase=$2
    local monitor_log="${REPORT_DIR}/logs/cycle${cycle}_${phase}_monitor.log"
    
    # Start background monitoring for key resources
    {
        while true; do
            local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
            echo "=== $timestamp ===" >> "$monitor_log"
            
            # Monitor managed clusters
            echo "Managed Clusters (Primary):" >> "$monitor_log"
            kubectl --context "$PRIMARY_CONTEXT" get managedclusters -o wide >> "$monitor_log" 2>&1
            echo "" >> "$monitor_log"
            
            echo "Managed Clusters (Secondary):" >> "$monitor_log"
            kubectl --context "$SECONDARY_CONTEXT" get managedclusters -o wide >> "$monitor_log" 2>&1
            echo "" >> "$monitor_log"
            
            # Monitor backup/restore status
            echo "Backup Schedule (Primary):" >> "$monitor_log"
            kubectl --context "$PRIMARY_CONTEXT" get backupschedules -n "$BACKUP_NAMESPACE" -o wide >> "$monitor_log" 2>&1
            echo "" >> "$monitor_log"
            
            echo "Restore Status (Secondary):" >> "$monitor_log"
            kubectl --context "$SECONDARY_CONTEXT" get restores -n "$BACKUP_NAMESPACE" -o wide >> "$monitor_log" 2>&1
            echo "" >> "$monitor_log"
            
            # Monitor observability (if enabled)
            if kubectl --context "$PRIMARY_CONTEXT" get namespace "$OBSERVABILITY_NAMESPACE" &>/dev/null; then
                echo "Observability Deployments (Primary):" >> "$monitor_log"
                kubectl --context "$PRIMARY_CONTEXT" get deployments -n "$OBSERVABILITY_NAMESPACE" -o wide >> "$monitor_log" 2>&1
                echo "" >> "$monitor_log"
            fi
            
            if kubectl --context "$SECONDARY_CONTEXT" get namespace "$OBSERVABILITY_NAMESPACE" &>/dev/null; then
                echo "Observability Deployments (Secondary):" >> "$monitor_log"
                kubectl --context "$SECONDARY_CONTEXT" get deployments -n "$OBSERVABILITY_NAMESPACE" -o wide >> "$monitor_log" 2>&1
                echo "" >> "$monitor_log"
            fi
            
            echo "----------------------------------------" >> "$monitor_log"
            sleep "$MONITORING_INTERVAL"
        done
    } &
    
    # Store monitor PID for cleanup
    echo $! > "${REPORT_DIR}/.monitor_${cycle}_${phase}.pid"
}

stop_phase_monitoring() {
    local cycle=$1
    local phase=$2
    local pid_file="${REPORT_DIR}/.monitor_${cycle}_${phase}.pid"
    
    if [[ -f "$pid_file" ]]; then
        local pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
        fi
        rm -f "$pid_file"
    fi
}

collect_metrics() {
    local cycle=$1
    local phase=$2
    local metrics_file="${REPORT_DIR}/metrics/cycle${cycle}_${phase}.json"
    
    # Collect resource metrics
    {
        echo "{"
        echo "  \"cycle\": $cycle,"
        echo "  \"phase\": \"$phase\","
        echo "  \"timestamp\": \"$(date -Iseconds)\","
        
        # Managed cluster counts
        local primary_mc_count=$(kubectl --context "$PRIMARY_CONTEXT" get managedclusters --no-headers 2>/dev/null | grep -v "$LOCAL_CLUSTER_NAME" | wc -l || echo "0")
        local secondary_mc_count=$(kubectl --context "$SECONDARY_CONTEXT" get managedclusters --no-headers 2>/dev/null | grep -v "$LOCAL_CLUSTER_NAME" | wc -l || echo "0")
        echo "  \"primary_managed_clusters\": $primary_mc_count,"
        echo "  \"secondary_managed_clusters\": $secondary_mc_count,"
        
        # Available cluster counts
        local primary_available=$(kubectl --context "$PRIMARY_CONTEXT" get managedclusters -o json 2>/dev/null | jq -r --arg LOCAL "$LOCAL_CLUSTER_NAME" '[.items[] | select(.metadata.name != $LOCAL) | select(.status.conditions[]? | select(.type=="ManagedClusterConditionAvailable" and .status=="True"))] | length' 2>/dev/null || echo "0")
        local secondary_available=$(kubectl --context "$SECONDARY_CONTEXT" get managedclusters -o json 2>/dev/null | jq -r --arg LOCAL "$LOCAL_CLUSTER_NAME" '[.items[] | select(.metadata.name != $LOCAL) | select(.status.conditions[]? | select(.type=="ManagedClusterConditionAvailable" and .status=="True"))] | length' 2>/dev/null || echo "0")
        echo "  \"primary_available_clusters\": $primary_available,"
        echo "  \"secondary_available_clusters\": $secondary_available,"
        
        # Backup/restore status
        local backup_phase=$(kubectl --context "$PRIMARY_CONTEXT" get backupschedules -n "$BACKUP_NAMESPACE" -o jsonpath='{.items[0].status.phase}' 2>/dev/null || echo "unknown")
        local restore_phase=$(kubectl --context "$SECONDARY_CONTEXT" get restores -n "$BACKUP_NAMESPACE" --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].status.phase}' 2>/dev/null || echo "none")
        echo "  \"backup_phase\": \"$backup_phase\","
        echo "  \"restore_phase\": \"$restore_phase\""
        
        echo "}"
    } > "$metrics_file"
}

# =============================================================================
# Test Execution Functions
# =============================================================================

run_phase() {
    local cycle=$1
    local phase=$2
    local start_time=$(date +%s)
    local phase_start=$(date -Iseconds)
    
    log_message "Cycle $cycle: Starting phase '$phase'"
    
    # Start monitoring for this phase
    start_phase_monitoring "$cycle" "$phase"
    
    # Execute phase-specific commands
    local exit_code=0
    case "$phase" in
        "preflight")
            python acm_switchover.py \
                --primary-context "$PRIMARY_CONTEXT" \
                --secondary-context "$SECONDARY_CONTEXT" \
                --method passive \
                --old-hub-action secondary \
                --validate-only \
                --verbose > "${REPORT_DIR}/logs/cycle${cycle}_${phase}.log" 2>&1 || exit_code=$?
            ;;
        "primary_prep")
            # This phase is part of normal execution - handled in run_switchover
            ;;
        "activation")
            # This phase is part of normal execution - handled in run_switchover
            ;;
        "post_activation")
            # This phase is part of normal execution - handled in run_switchover
            ;;
        "finalization")
            # This phase is part of normal execution - handled in run_switchover
            ;;
        *)
            log_error "Unknown phase: $phase"
            exit_code=1
            ;;
    esac
    
    # Stop monitoring
    stop_phase_monitoring "$cycle" "$phase"
    
    # Collect metrics
    collect_metrics "$cycle" "$phase"
    
    local end_time=$(date +%s)
    local duration=$((end_time - start_time))
    local phase_end=$(date -Iseconds)
    
    # Record results
    echo "$cycle,$phase,$exit_code,$phase_start,$phase_end,$duration,$exit_code" >> "${REPORT_DIR}/cycle_results.csv"
    PHASE_TIMINGS["${cycle}_${phase}"]=$duration
    
    if [[ $exit_code -eq 0 ]]; then
        log_message "✅ Cycle $cycle: Phase '$phase' completed in ${duration}s"
    else
        log_message "❌ Cycle $cycle: Phase '$phase' failed in ${duration}s (exit code: $exit_code)"
    fi
    
    return $exit_code
}

run_switchover() {
    local cycle=$1
    local method=${2:-passive}
    local old_hub_action=${3:-secondary}
    local start_time=$(date +%s)
    
    log_message "Cycle $cycle: Starting full switchover execution"
    log_message "Method: $method, Old Hub Action: $old_hub_action"
    
    # Reset state for clean run
    local state_file=".state/switchover-${PRIMARY_CONTEXT}__${SECONDARY_CONTEXT}.json"
    rm -f "$state_file" || true
    
    # Start comprehensive monitoring
    start_phase_monitoring "$cycle" "switchover"
    
    # Execute switchover
    local exit_code=0
    python acm_switchover.py \
        --primary-context "$PRIMARY_CONTEXT" \
        --secondary-context "$SECONDARY_CONTEXT" \
        --method "$method" \
        --old-hub-action "$old_hub_action" \
        --verbose > "${REPORT_DIR}/logs/cycle${cycle}_switchover.log" 2>&1 || exit_code=$?
    
    # Stop monitoring
    stop_phase_monitoring "$cycle" "switchover"
    
    local end_time=$(date +%s)
    local duration=$((end_time - start_time))
    
    # Record results
    local phase_start=$(date -Iseconds -d "@$start_time")
    local phase_end=$(date -Iseconds -d "@$end_time")
    echo "$cycle,switchover,$exit_code,$phase_start,$phase_end,$duration,$exit_code" >> "${REPORT_DIR}/cycle_results.csv"
    PHASE_TIMINGS["${cycle}_switchover"]=$duration
    
    if [[ $exit_code -eq 0 ]]; then
        log_message "✅ Cycle $cycle: Switchover completed successfully in ${duration}s"
        CYCLE_RESULTS[$cycle]="SUCCESS"
    else
        log_message "❌ Cycle $cycle: Switchover failed in ${duration}s (exit code: $exit_code)"
        CYCLE_RESULTS[$cycle]="FAILED"
    fi
    
    # Save state file for analysis
    if [[ -f "$state_file" ]]; then
        cp "$state_file" "${REPORT_DIR}/states/cycle${cycle}_state.json"
    fi
    
    return $exit_code
}

validate_switchover() {
    local cycle=$1
    
    log_message "Cycle $cycle: Running post-switchover validation"
    
    # Run postflight checks
    local exit_code=0
    "${SCRIPT_DIR}/../../scripts/postflight-check.sh" \
        --old-hub-context "$PRIMARY_CONTEXT" \
        --new-hub-context "$SECONDARY_CONTEXT" > "${REPORT_DIR}/logs/cycle${cycle}_postflight.log" 2>&1 || exit_code=$?
    
    if [[ $exit_code -eq 0 ]]; then
        log_message "✅ Cycle $cycle: Post-flight validation passed"
    else
        log_message "❌ Cycle $cycle: Post-flight validation failed (exit code: $exit_code)"
    fi
    
    return $exit_code
}

reset_environment() {
    local cycle=$1
    
    log_message "Cycle $cycle: Resetting environment for next cycle"
    
    # For testing, we'll swap contexts to simulate returning to original state
    local temp_primary="$PRIMARY_CONTEXT"
    PRIMARY_CONTEXT="$SECONDARY_CONTEXT"
    SECONDARY_CONTEXT="$temp_primary"
    
    log_message "Swapped contexts: Primary=$PRIMARY_CONTEXT, Secondary=$SECONDARY_CONTEXT"
}

# =============================================================================
# Reporting Functions
# =============================================================================

generate_summary_report() {
    local summary_file="${REPORT_DIR}/summary_report.txt"
    
    log_message "Generating summary report..."
    
    {
        echo "ACM Switchover E2E Test Summary"
        echo "================================"
        echo "Started: $(date -d "@$TOTAL_START_TIME" '+%Y-%m-%d %H:%M:%S')"
        echo "Completed: $(date '+%Y-%m-%d %H:%M:%S')"
        echo "Total Duration: $(($(date +%s) - TOTAL_START_TIME)) seconds"
        echo ""
        echo "Configuration:"
        echo "  Primary Context: $PRIMARY_CONTEXT"
        echo "  Secondary Context: $SECONDARY_CONTEXT"
        echo "  Cycles Executed: $CYCLES"
        echo "  Monitoring Interval: ${MONITORING_INTERVAL}s"
        echo ""
        echo "Cycle Results:"
        for ((i=1; i<=CYCLES; i++)); do
            local result="${CYCLE_RESULTS[$i]:-UNKNOWN}"
            echo "  Cycle $i: $result"
        done
        echo ""
        echo "Phase Timing Summary:"
        for key in "${!PHASE_TIMINGS[@]}"; do
            echo "  $key: ${PHASE_TIMINGS[$key]}s"
        done
        echo ""
        echo "Files Generated:"
        echo "  Logs: ${REPORT_DIR}/logs/"
        echo "  States: ${REPORT_DIR}/states/"
        echo "  Metrics: ${REPORT_DIR}/metrics/"
        echo "  Results: ${REPORT_DIR}/cycle_results.csv"
        echo ""
        echo "Next Steps:"
        echo "  1. Review detailed logs in ${REPORT_DIR}/logs/"
        echo "  2. Analyze metrics in ${REPORT_DIR}/metrics/"
        echo "  3. Check state files in ${REPORT_DIR}/states/"
        echo "  4. Validate all cycles completed successfully"
    } > "$summary_file"
    
    log_message "Summary report generated: $summary_file"
}

# =============================================================================
# Main Execution
# =============================================================================

main() {
    # Print deprecation warning
    print_deprecation_warning
    
    TOTAL_START_TIME=$(date +%s)
    
    log_message "Starting ACM Switchover E2E Test Orchestrator"
    log_message "Configuration: Primary=$PRIMARY_CONTEXT, Secondary=$SECONDARY_CONTEXT, Cycles=$CYCLES"
    
    # Setup
    setup_environment
    validate_environment
    
    # Run test cycles
    local successful_cycles=0
    local failed_cycles=0
    
    for ((cycle=1; cycle<=CYCLES; cycle++)); do
        log_message "=== Starting Cycle $cycle/$CYCLES ==="
        
        # Run preflight validation
        if ! run_phase "$cycle" "preflight"; then
            log_error "Cycle $cycle: Pre-flight validation failed, skipping cycle"
            ((failed_cycles++))
            continue
        fi
        
        # Run switchover
        if run_switchover "$cycle" "passive" "secondary"; then
            ((successful_cycles++))
            
            # Validate successful switchover
            validate_switchover "$cycle"
        else
            ((failed_cycles++))
        fi
        
        # Reset environment for next cycle (except last cycle)
        if [[ $cycle -lt $CYCLES ]]; then
            reset_environment "$cycle"
        fi
        
        log_message "=== Completed Cycle $cycle/$CYCLES ==="
        echo ""
        
        # Brief pause between cycles
        if [[ $cycle -lt $CYCLES ]]; then
            log_message "Pausing 30 seconds before next cycle..."
            sleep 30
        fi
    done
    
    # Generate final report
    generate_summary_report
    
    log_message "=== E2E Test Suite Completed ==="
    log_message "Successful cycles: $successful_cycles/$CYCLES"
    log_message "Failed cycles: $failed_cycles/$CYCLES"
    log_message "Results directory: $REPORT_DIR"
    
    if [[ $failed_cycles -eq 0 ]]; then
        log_message "🎉 All cycles completed successfully!"
        return 0
    else
        log_message "⚠️  Some cycles failed. Check logs for details."
        return 1
    fi
}

# Handle interruption gracefully
trap 'log_message "Test interrupted by user"; exit 130' INT TERM

# Execute main function
main "$@"
