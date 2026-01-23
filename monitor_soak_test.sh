#!/bin/bash
# Monitor the 12-hour soak test progress

echo "========================================"
echo "ACM Switchover - 12 Hour Soak Test Monitor"
echo "========================================"
echo ""

# Check if test is running
if ps aux | grep -E "pytest.*e2e.*run-hours" | grep -v grep > /dev/null; then
    echo "✓ Test is RUNNING"
    ps aux | grep -E "pytest.*e2e.*run-hours" | grep -v grep | awk '{printf "  PID: %s, CPU: %s%%, Memory: %s%%, Runtime: %s\n", $2, $3, $4, $10}'
else
    echo "✗ Test is NOT running"
fi

echo ""
echo "----------------------------------------"
echo "Latest Log Output (last 30 lines):"
echo "----------------------------------------"
if [ -f soak_test_12h.log ]; then
    tail -30 soak_test_12h.log
else
    echo "Log file not found"
fi

echo ""
echo "----------------------------------------"
echo "Test Artifacts:"
echo "----------------------------------------"
if [ -d ./e2e-soak-test-12h ]; then
    echo "Output directory exists:"
    ls -lh ./e2e-soak-test-12h/
    
    # Check for run directories
    for run_dir in ./e2e-soak-test-12h/run_*/; do
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
echo "  View live log:        tail -f soak_test_12h.log"
echo "  Stop test:            pkill -f 'pytest.*e2e.*run-hours'"
echo "  Analyze results:      python tests/e2e/e2e_analyzer.py --results-dir ./e2e-soak-test-12h/run_*"
echo ""
