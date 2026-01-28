#!/bin/bash
# Monitor the 12-hour soak test progress

# Configurable paths
RESULTS_DIR="${RESULTS_DIR:-./e2e-soak-test-12h}"
LOG_FILE="${LOG_FILE:-soak_test_12h.log}"

echo "========================================"
echo "ACM Switchover - 12 Hour Soak Test Monitor"
echo "========================================"
echo ""

# Check if test is running (support both pytest and bash runner)
PYTEST_PIDS=$(pgrep -f "pytest.*e2e.*run-hours" 2>/dev/null || true)
BASH_PIDS=$(pgrep -f "run_12h_soak_test.sh" 2>/dev/null || true)
SWITCHOVER_PIDS=$(pgrep -f "acm_switchover.py.*--primary-context" 2>/dev/null || true)

if [[ -n "$PYTEST_PIDS" ]]; then
    echo "✓ Test is RUNNING (pytest mode)"
    ps -o pid,pcpu,pmem,etime --no-headers -p $PYTEST_PIDS 2>/dev/null | while read pid cpu mem etime; do
        printf "  PID: %s, CPU: %s%%, Memory: %s%%, Elapsed: %s\n" "$pid" "$cpu" "$mem" "$etime"
    done
elif [[ -n "$BASH_PIDS" ]]; then
    echo "✓ Test is RUNNING (bash runner mode)"
    ps -o pid,pcpu,pmem,etime --no-headers -p $BASH_PIDS 2>/dev/null | while read pid cpu mem etime; do
        printf "  Runner PID: %s, CPU: %s%%, Memory: %s%%, Elapsed: %s\n" "$pid" "$cpu" "$mem" "$etime"
    done
    if [[ -n "$SWITCHOVER_PIDS" ]]; then
        ps -o pid,pcpu,pmem,etime --no-headers -p $SWITCHOVER_PIDS 2>/dev/null | while read pid cpu mem etime; do
            printf "  Active switchover PID: %s, CPU: %s%%, Memory: %s%%, Elapsed: %s\n" "$pid" "$cpu" "$mem" "$etime"
        done
    fi
else
    echo "✗ Test is NOT running"
fi

echo ""
echo "----------------------------------------"
echo "Latest Log Output (last 30 lines):"
echo "----------------------------------------"
# Check pytest log first, then fall back to latest cycle log
if [ -f "$LOG_FILE" ]; then
    tail -30 "$LOG_FILE"
elif ls "$RESULTS_DIR"/cycle_*.log 1>/dev/null 2>&1; then
    LATEST_CYCLE=$(ls -t "$RESULTS_DIR"/cycle_*.log 2>/dev/null | head -1)
    echo "(Showing latest cycle log: $(basename "$LATEST_CYCLE"))"
    tail -30 "$LATEST_CYCLE"
else
    echo "Log file not found"
fi

echo ""
echo "----------------------------------------"
echo "Test Artifacts:"
echo "----------------------------------------"
if [ -d "$RESULTS_DIR" ]; then
    echo "Output directory exists: $RESULTS_DIR"
    ls -lh "$RESULTS_DIR"/
    
    # Show cycle progress
    TOTAL_CYCLES=$(ls -1 "$RESULTS_DIR"/cycle_*.log 2>/dev/null | wc -l)
    PASSED=$(grep -l "SWITCHOVER COMPLETED SUCCESSFULLY" "$RESULTS_DIR"/cycle_*.log 2>/dev/null | wc -l)
    if [ "$TOTAL_CYCLES" -gt 0 ]; then
        echo ""
        echo "Cycle Progress: $PASSED/$TOTAL_CYCLES completed successfully"
    fi
    
    # Check for run directories (pytest mode)
    for run_dir in "$RESULTS_DIR"/run_*/; do
        if [ -d "$run_dir" ]; then
            echo ""
            echo "Run: $(basename "$run_dir")"
            [ -f "$run_dir/summary.json" ] && echo "  - summary.json exists"
            [ -f "$run_dir/cycle_results.csv" ] && echo "  - cycle_results.csv: $(wc -l < "$run_dir/cycle_results.csv") lines"
            [ -f "$run_dir/metrics/metrics.jsonl" ] && echo "  - metrics.jsonl: $(wc -l < "$run_dir/metrics/metrics.jsonl") entries"
            [ -d "$run_dir/states" ] && echo "  - states: $(ls -1 "$run_dir/states" | wc -l) files"
        fi
    done
else
    echo "No output directory yet (test may still be initializing)"
fi

echo ""
echo "----------------------------------------"
echo "Commands:"
echo "----------------------------------------"
echo "  Monitor continuously: watch -n 30 ./monitor_soak_test.sh"
echo "  View live log:        tail -f $LOG_FILE  OR  tail -f $RESULTS_DIR/cycle_N.log"
echo "  Stop test:            pkill -f 'run_12h_soak_test.sh' OR pkill -f 'pytest.*e2e.*run-hours'"
echo "  Analyze results:      ./analyze_soak_results.sh  OR  python tests/e2e/e2e_analyzer.py --results-dir $RESULTS_DIR/run_*"
echo ""
echo "  Set custom paths:     RESULTS_DIR=/path/to/results LOG_FILE=/path/to/log ./monitor_soak_test.sh"
echo ""
