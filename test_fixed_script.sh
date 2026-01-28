#!/bin/bash
# Quick test to verify state cleanup works

set -u

echo "Testing fixed soak test script..."
echo ""

# Run 2 cycles to verify state cleanup
export DURATION_SECONDS=600  # 10 minutes for testing

START_TIME=$(date +%s)
CYCLE=0

RUN_ID="test_$(date +%Y%m%d_%H%M%S)"
RESULTS_DIR="./e2e-soak-test-test/${RUN_ID}"
mkdir -p "${RESULTS_DIR}"

echo "Logs will be kept in: ${RESULTS_DIR}"
echo ""

while [ $CYCLE -lt 2 ]; do
    CURRENT_TIME=$(date +%s)
    ELAPSED=$((CURRENT_TIME - START_TIME))
    
    if [ $ELAPSED -ge $DURATION_SECONDS ]; then
        break
    fi
    
    CYCLE=$((CYCLE + 1))
    echo "========================================"
    echo "Test Cycle $CYCLE"
    echo "========================================"
    
    if [ $((CYCLE % 2)) -eq 1 ]; then
        PRIMARY="mgmt1"
        SECONDARY="mgmt2"
    else
        PRIMARY="mgmt2"
        SECONDARY="mgmt1"
    fi
    
    echo "Checking state files before cleanup:"
    ls -la .state/switchover-${PRIMARY}__${SECONDARY}.json 2>/dev/null && echo "  State file exists" || echo "  No state file"
    
    echo "Cleaning state files..."
    rm -f .state/switchover-${PRIMARY}__${SECONDARY}.json
    rm -f .state/switchover-${SECONDARY}__${PRIMARY}.json
    
    echo "State files after cleanup:"
    ls -la .state/switchover-${PRIMARY}__${SECONDARY}.json 2>/dev/null && echo "  ERROR: State file still exists!" || echo "  ✓ State file removed"
    
    echo "Running switchover..."
    LOG_FILE="${RESULTS_DIR}/cycle_${CYCLE}.log"
    ./.venv/bin/python acm_switchover.py \
        --primary-context "$PRIMARY" \
        --secondary-context "$SECONDARY" \
        --method passive \
        --old-hub-action secondary \
        > "${LOG_FILE}" 2>&1
    EXIT_CODE=$?

    # Classify what happened
    RUN_TYPE="unknown"
    if grep -q "Resuming recently completed switchover" "${LOG_FILE}"; then
        RUN_TYPE="resumed"
    elif grep -q "DETECTED STALE COMPLETED STATE" "${LOG_FILE}"; then
        RUN_TYPE="stale_state_blocked"
    elif grep -q "PHASE 1: PRE-FLIGHT" "${LOG_FILE}"; then
        RUN_TYPE="real"
    fi

    if grep -q "SWITCHOVER COMPLETED SUCCESSFULLY" "${LOG_FILE}"; then
        SUCCESS=1
    else
        SUCCESS=0
    fi

    if [ "${SUCCESS}" -eq 1 ]; then
        echo "✓ Cycle ${CYCLE}: SUCCESS (${RUN_TYPE})"
    else
        echo "✗ Cycle ${CYCLE}: FAILED (exit=${EXIT_CODE}, ${RUN_TYPE})"
        echo "  Log: ${LOG_FILE}"
        echo "  Last ERROR/WARNING/Traceback lines:"
        grep -E "(ERROR|Traceback|Exception|VALIDATION FAILED|Use --force)" "${LOG_FILE}" | tail -8 | sed 's/^/    /' || true
        echo "  Last 25 log lines:"
        tail -25 "${LOG_FILE}" | sed 's/^/    /'
    fi
    
    echo ""
    
    if [ $CYCLE -lt 2 ]; then
        echo "Waiting 10 seconds before next cycle..."
        sleep 10
    fi
done

echo "========================================"
echo "Test Complete"
echo "========================================"
echo "Check logs in: ${RESULTS_DIR}"
echo ""
echo "Summary:"
TOTAL=$(ls -1 "${RESULTS_DIR}"/cycle_*.log 2>/dev/null | wc -l)
REAL=$(grep -l "PHASE 1: PRE-FLIGHT" "${RESULTS_DIR}"/cycle_*.log 2>/dev/null | wc -l)
RESUMED=$(grep -l "Resuming recently completed switchover" "${RESULTS_DIR}"/cycle_*.log 2>/dev/null | wc -l)
STALE_BLOCKED=$(grep -l "DETECTED STALE COMPLETED STATE" "${RESULTS_DIR}"/cycle_*.log 2>/dev/null | wc -l)
SUCCEEDED=$(grep -l "SWITCHOVER COMPLETED SUCCESSFULLY" "${RESULTS_DIR}"/cycle_*.log 2>/dev/null | wc -l)
FAILED=$((TOTAL - SUCCEEDED))

echo "  Total cycles: ${TOTAL}"
echo "  Successful: ${SUCCEEDED}"
echo "  Failed: ${FAILED}"
echo "  Real attempts (preflight started): ${REAL}"
echo "  Resumed: ${RESUMED}"
echo "  Blocked by stale completed state: ${STALE_BLOCKED}"
echo ""
echo "Note: This script only proves state cleanup; failures typically mean the switchover itself failed preflight/activation and needs investigation in the per-cycle logs."
