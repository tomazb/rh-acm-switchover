#!/bin/bash
# 15-Cycle ACM Hub Switchover Validation Test
#
# Validates recent code changes (ArgoCD auto-detection, resume hardening)
# by running 15 consecutive real alternating switchovers between mgmt1↔mgmt2.
#
# Usage:
#   ./run_15_switchover_test.sh [--stop-on-failure] [--skip-postflight]
#
# Environment:
#   Hubs:     mgmt1 (Hub A), mgmt2 (Hub B)
#   Managed:  prod1, prod2, prod3
#   Method:   passive (continuous sync)
#   ArgoCD:   --argocd-manage (ACM-touching apps detected on both hubs)

set -euo pipefail
cd "$(dirname "$0")"

# =============================================================================
# Configuration
# =============================================================================
TOTAL_CYCLES=15
COOLDOWN_SECONDS=30
OUTPUT_DIR="./e2e-15-switchover"
PYTHON="${PYTHON:-$(command -v .venv/bin/python || command -v python3)}"
SCRIPT_DIR="./scripts"

HUB_A="mgmt2"
HUB_B="mgmt1"

STOP_ON_FAILURE=false
SKIP_POSTFLIGHT=false

for arg in "$@"; do
    case "$arg" in
        --stop-on-failure) STOP_ON_FAILURE=true ;;
        --skip-postflight) SKIP_POSTFLIGHT=true ;;
        *) echo "Unknown argument: $arg"; exit 2 ;;
    esac
done

# =============================================================================
# Bookkeeping
# =============================================================================
START_TIME=$(date +%s)
PASSED=0
FAILED=0
declare -a CYCLE_RESULTS=()
declare -a CYCLE_DURATIONS=()

mkdir -p "$OUTPUT_DIR"

# Summary file for machine-readable results
SUMMARY_FILE="$OUTPUT_DIR/summary.json"

# =============================================================================
# Functions
# =============================================================================
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

write_summary() {
    local end_time
    end_time=$(date +%s)
    local elapsed=$((end_time - START_TIME))

    cat > "$SUMMARY_FILE" << SUMEOF
{
  "total_cycles": $TOTAL_CYCLES,
  "passed": $PASSED,
  "failed": $FAILED,
  "success_rate": $(awk "BEGIN {printf \"%.1f\", ($PASSED / ($PASSED + $FAILED)) * 100}"),
  "elapsed_seconds": $elapsed,
  "cycle_results": [$(IFS=,; echo "${CYCLE_RESULTS[*]}")],
  "cycle_durations_seconds": [$(IFS=,; echo "${CYCLE_DURATIONS[*]}")],
  "argocd_manage": true,
  "method": "passive",
  "hub_a": "$HUB_A",
  "hub_b": "$HUB_B"
}
SUMEOF
}

print_banner() {
    echo ""
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║   ACM Switchover - 15-Cycle Validation Test              ║"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo "  Started:  $(date)"
    echo "  Hubs:     $HUB_A ↔ $HUB_B"
    echo "  Method:   passive + --argocd-manage"
    echo "  Cycles:   $TOTAL_CYCLES"
    echo "  Cooldown: ${COOLDOWN_SECONDS}s between cycles"
    echo ""
}

run_cycle() {
    local cycle=$1
    local primary=$2
    local secondary=$3
    local log_file
    log_file=$(printf "%s/cycle_%02d.log" "$OUTPUT_DIR" "$cycle")
    local cycle_start
    cycle_start=$(date +%s)

    log "━━━ Cycle $cycle/$TOTAL_CYCLES: $primary → $secondary ━━━"

    # Clean state files for this direction
    rm -f ".state/switchover-${primary}__${secondary}.json"
    rm -f ".state/switchover-${primary}__${secondary}.json.lock"
    rm -f ".state/switchover-${primary}__${secondary}.json.run.lock"

    local exit_code=0
    $PYTHON acm_switchover.py \
        --primary-context "$primary" \
        --secondary-context "$secondary" \
        --method passive \
        --old-hub-action secondary \
        --argocd-manage \
        --force \
        > "$log_file" 2>&1 || exit_code=$?

    local cycle_end
    cycle_end=$(date +%s)
    local duration=$((cycle_end - cycle_start))
    CYCLE_DURATIONS+=("$duration")

    if [ $exit_code -eq 0 ]; then
        PASSED=$((PASSED + 1))
        CYCLE_RESULTS+=('"pass"')
        log "✓ Cycle $cycle PASSED (${duration}s)"
    else
        FAILED=$((FAILED + 1))
        CYCLE_RESULTS+=('"fail"')
        log "✗ Cycle $cycle FAILED (${duration}s, exit=$exit_code)"
        log "  Log: $log_file"
        log "  Last error:"
        grep -E "ERROR|FAILED|Exception|Traceback" "$log_file" 2>/dev/null | tail -5 | sed 's/^/    /'
    fi

    return $exit_code
}

run_postflight() {
    local cycle=$1
    local primary=$2
    local secondary=$3
    local pf_log
    pf_log=$(printf "%s/cycle_%02d_postflight.log" "$OUTPUT_DIR" "$cycle")

    if $SKIP_POSTFLIGHT; then
        return 0
    fi

    log "  Running postflight check..."
    if "$SCRIPT_DIR/postflight-check.sh" \
        --primary-context "$primary" \
        --secondary-context "$secondary" \
        --method passive \
        > "$pf_log" 2>&1; then
        log "  ✓ Postflight passed"
    else
        log "  ⚠ Postflight warning (see $pf_log)"
    fi
}

# =============================================================================
# Main
# =============================================================================
print_banner

# Initial discovery snapshot
log "Running initial hub discovery..."
"$SCRIPT_DIR/discover-hub.sh" --auto > "$OUTPUT_DIR/discovery_before.log" 2>&1 || true
log "Discovery saved to $OUTPUT_DIR/discovery_before.log"
echo ""

for cycle in $(seq 1 $TOTAL_CYCLES); do
    # Alternate direction
    if [ $((cycle % 2)) -eq 1 ]; then
        PRIMARY="$HUB_A"
        SECONDARY="$HUB_B"
    else
        PRIMARY="$HUB_B"
        SECONDARY="$HUB_A"
    fi

    cycle_failed=false
    if ! run_cycle "$cycle" "$PRIMARY" "$SECONDARY"; then
        cycle_failed=true
    fi

    # Postflight: after switchover, new primary = SECONDARY (destination hub)
    run_postflight "$cycle" "$SECONDARY" "$PRIMARY"

    # Progress
    local_elapsed=$(( $(date +%s) - START_TIME ))
    log "  Progress: $PASSED passed, $FAILED failed, ${local_elapsed}s elapsed"

    if $cycle_failed && $STOP_ON_FAILURE; then
        log ""
        log "⛔ Stopping on first failure (--stop-on-failure)"
        log "  Running discovery for diagnostics..."
        "$SCRIPT_DIR/discover-hub.sh" --auto > "$OUTPUT_DIR/discovery_failure.log" 2>&1 || true
        break
    fi

    # Cooldown (skip after last cycle)
    if [ "$cycle" -lt "$TOTAL_CYCLES" ]; then
        log "  Cooldown ${COOLDOWN_SECONDS}s..."
        sleep "$COOLDOWN_SECONDS"
    fi
    echo ""
done

# Final discovery
log "Running final hub discovery..."
"$SCRIPT_DIR/discover-hub.sh" --auto > "$OUTPUT_DIR/discovery_after.log" 2>&1 || true

# Write machine-readable summary
write_summary

# Print summary
END_TIME=$(date +%s)
TOTAL_ELAPSED=$((END_TIME - START_TIME))
HOURS=$((TOTAL_ELAPSED / 3600))
MINS=$(((TOTAL_ELAPSED % 3600) / 60))
SECS=$((TOTAL_ELAPSED % 60))

echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║   Results                                                 ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo "  Cycles:       $TOTAL_CYCLES"
echo "  Passed:       $PASSED"
echo "  Failed:       $FAILED"
echo "  Success Rate: $(awk "BEGIN {printf \"%.1f\", ($PASSED / $TOTAL_CYCLES) * 100}")%"
echo "  Total Time:   ${HOURS}h ${MINS}m ${SECS}s"
echo ""
echo "  Logs:         $OUTPUT_DIR/"
echo "  Summary:      $SUMMARY_FILE"

if [ $FAILED -gt 0 ]; then
    echo ""
    echo "  Failed cycles:"
    for i in $(seq 0 $((${#CYCLE_RESULTS[@]} - 1))); do
        if [ "${CYCLE_RESULTS[$i]}" = '"fail"' ]; then
            printf "    Cycle %02d (%ss)\n" $((i + 1)) "${CYCLE_DURATIONS[$i]}"
        fi
    done
    exit 1
else
    echo ""
    echo "  🎉 All $TOTAL_CYCLES cycles passed!"
    exit 0
fi
