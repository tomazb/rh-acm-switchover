#!/bin/bash
# Analyze 12-hour soak test results

RESULTS_DIR="./e2e-soak-test-12h"

echo "========================================"
echo "ACM Switchover - 12h Soak Test Analysis"
echo "========================================"
echo ""

# Count total cycles
TOTAL_CYCLES=$(ls -1 ${RESULTS_DIR}/cycle_*.log 2>/dev/null | wc -l)
echo "Total Cycles: $TOTAL_CYCLES"

# Count passes and failures
PASSED=$(grep -l "SWITCHOVER COMPLETED SUCCESSFULLY" ${RESULTS_DIR}/cycle_*.log 2>/dev/null | wc -l)
FAILED=$((TOTAL_CYCLES - PASSED))

echo "Passed: $PASSED"
echo "Failed: $FAILED"
if [ "$TOTAL_CYCLES" -eq 0 ]; then
    echo "Success Rate: N/A (no cycles found)"
else
    echo "Success Rate: $(awk "BEGIN {printf \"%.2f\", ($PASSED/$TOTAL_CYCLES)*100}")%"
fi
echo ""

# Show failures if any
if [ $FAILED -gt 0 ]; then
    echo "========================================"
    echo "Failed Cycles:"
    echo "========================================"
    for log in ${RESULTS_DIR}/cycle_*.log; do
        if ! grep -q "SWITCHOVER COMPLETED SUCCESSFULLY" "$log"; then
            CYCLE=$(basename "$log" .log | sed 's/cycle_//')
            echo "  Cycle $CYCLE: $(basename $log)"
            echo "    Last error:"
            grep -E "ERROR|FAILED|Exception" "$log" | tail -3 | sed 's/^/      /'
            echo ""
        fi
    done
fi

# Timing analysis
echo "========================================"
echo "Timing Analysis:"
echo "========================================"

# Extract timing from logs - pass RESULTS_DIR to Python
RESULTS_DIR_ABS=$(cd "$RESULTS_DIR" 2>/dev/null && pwd || echo "$RESULTS_DIR")
cat > /tmp/analyze_timing.py << PYEOF
import re
import statistics
from pathlib import Path

results_dir = Path("$RESULTS_DIR_ABS")
timings = []
for log_file in sorted(results_dir.glob("cycle_*.log")):
    content = log_file.read_text()
    # Look for "Switchover completed at" or phase timing
    if "SWITCHOVER COMPLETED SUCCESSFULLY" in content:
        # Try to extract duration from state or logs
        # This is approximate - could be improved
        match = re.search(r'Started at: ([\d-]+T[\d:.]+(?:Z|[+-]\d\d:\d\d)?)', content)
        match2 = re.search(r'completed at: ([\d-]+T[\d:.]+(?:Z|[+-]\d\d:\d\d)?)', content)
        if match and match2:
            from datetime import datetime
            def parse_iso(value: str) -> datetime:
                if value.endswith("Z"):
                    value = value[:-1] + "+00:00"
                return datetime.fromisoformat(value)

            start = parse_iso(match.group(1))
            end = parse_iso(match2.group(1))
            duration = (end - start).total_seconds()
            timings.append(duration)

if timings:
    print(f"Cycles analyzed: {len(timings)}")
    print(f"Average duration: {statistics.mean(timings):.1f}s ({statistics.mean(timings)/60:.1f}m)")
    print(f"Median duration: {statistics.median(timings):.1f}s ({statistics.median(timings)/60:.1f}m)")
    print(f"Min duration: {min(timings):.1f}s ({min(timings)/60:.1f}m)")
    print(f"Max duration: {max(timings):.1f}s ({max(timings)/60:.1f}m)")
    if len(timings) > 1:
        print(f"Std deviation: {statistics.stdev(timings):.1f}s")
else:
    print("No timing data available")
PYEOF

python3 /tmp/analyze_timing.py
rm /tmp/analyze_timing.py
echo ""

# Check for warnings/issues
echo "========================================"
echo "Common Warnings/Issues:"
echo "========================================"
grep -h "WARNING" ${RESULTS_DIR}/cycle_*.log 2>/dev/null | sort | uniq -c | sort -rn | head -10
echo ""

# Phase-specific analysis
echo "========================================"
echo "Phase Completion Summary:"
echo "========================================"
for phase in "PREFLIGHT" "PRIMARY_PREP" "ACTIVATION" "POST_ACTIVATION" "FINALIZATION"; do
    COUNT=$(grep -c "PHASE.*${phase}" ${RESULTS_DIR}/cycle_*.log 2>/dev/null | awk -F: '{sum+=$2} END {print sum+0}')
    COMPLETE=$(grep -c "${phase}.*complete" ${RESULTS_DIR}/cycle_*.log 2>/dev/null | awk -F: '{sum+=$2} END {print sum+0}')
    echo "  $phase: $COUNT started, $COMPLETE completed"
done
echo ""

# Hub status at end
echo "========================================"
echo "Final Hub Status:"
echo "========================================"
echo "Run: ./scripts/discover-hub.sh --auto"
echo ""

echo "========================================"
echo "Detailed Analysis Commands:"
echo "========================================"
echo "View specific cycle:"
echo "  less ${RESULTS_DIR}/cycle_N.log"
echo ""
echo "Search for errors:"
echo "  grep -i error ${RESULTS_DIR}/cycle_*.log | less"
echo ""
echo "Check observability issues:"
echo "  grep -i observability ${RESULTS_DIR}/cycle_*.log | grep -i warning"
echo ""
echo "View all failed cycles:"
echo "  for f in ${RESULTS_DIR}/cycle_*.log; do grep -L 'COMPLETED SUCCESSFULLY' \$f; done"
