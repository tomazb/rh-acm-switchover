#!/bin/bash
# 12-Hour ACM Hub Switchover Soak Test
# Runs continuous switchover cycles for 12 hours

set -e
cd "$(dirname "$0")"

echo "========================================"
echo "ACM Hub Switchover - 12 Hour Soak Test"
echo "========================================"
echo "Starting at: $(date)"
echo "Duration: 12 hours"
echo "Output: ./e2e-soak-test-12h/"
echo ""

# Start time
START_TIME=$(date +%s)
DURATION_SECONDS=$((12 * 3600))  # 12 hours in seconds
CYCLE=0

# Create output directory
mkdir -p ./e2e-soak-test-12h

# Run cycles
while true; do
    CURRENT_TIME=$(date +%s)
    ELAPSED=$((CURRENT_TIME - START_TIME))
    
    # Check if we've exceeded 12 hours
    if [ $ELAPSED -ge $DURATION_SECONDS ]; then
        echo ""
        echo "========================================"
        echo "12-Hour Soak Test Completed"
        echo "========================================"
        echo "Total cycles completed: $CYCLE"
        echo "Total time: $((ELAPSED / 3600))h $((($ELAPSED % 3600) / 60))m $((ELAPSED % 60))s"
        break
    fi
    
    CYCLE=$((CYCLE + 1))
    REMAINING=$((DURATION_SECONDS - ELAPSED))
    HOURS=$((REMAINING / 3600))
    MINS=$((($REMAINING % 3600) / 60))
    
    echo "================================================"
    echo "Cycle $CYCLE - Remaining time: ${HOURS}h ${MINS}m"
    echo "Time: $(date)"
    echo "================================================"
    
    # Determine primary/secondary based on cycle parity
    if [ $((CYCLE % 2)) -eq 1 ]; then
        PRIMARY="mgmt1"
        SECONDARY="mgmt2"
        echo "Direction: mgmt1 → mgmt2"
    else
        PRIMARY="mgmt2"
        SECONDARY="mgmt1"
        echo "Direction: mgmt2 → mgmt1"
    fi
    
    # Clean up state files to force fresh switchover
    echo "Cleaning state files..."
    rm -f .state/switchover-${PRIMARY}__${SECONDARY}.json
    rm -f .state/switchover-${SECONDARY}__${PRIMARY}.json
    
    # Run single switchover cycle
    if ./.venv/bin/python acm_switchover.py \
        --primary-context "$PRIMARY" \
        --secondary-context "$SECONDARY" \
        --method passive \
        --old-hub-action secondary \
        > "./e2e-soak-test-12h/cycle_${CYCLE}.log" 2>&1; then
        echo "✓ Cycle $CYCLE PASSED"
    else
        echo "✗ Cycle $CYCLE FAILED (see ./e2e-soak-test-12h/cycle_${CYCLE}.log)"
    fi
    
    echo ""
    
    # 30 second cooldown
    sleep 30
done

echo "Logs: ./e2e-soak-test-12h/"
