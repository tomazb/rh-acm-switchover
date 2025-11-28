#!/bin/bash
#
# Unit tests for lib-common.sh
#
# This script tests the shared library functions used by the ACM switchover scripts.
# It validates that all functions work correctly in isolation.
#
# Usage:
#   ./tests/test_lib_common.sh
#
# Exit codes:
#   0 - All tests passed
#   1 - One or more tests failed

set -euo pipefail

# Colors for test output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Test counters
TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_COMMON="${SCRIPT_DIR}/../scripts/lib-common.sh"
CONSTANTS="${SCRIPT_DIR}/../scripts/constants.sh"

# Test helper functions
test_pass() {
    ((TESTS_PASSED++)) || true
    echo -e "${GREEN}✓ PASS:${NC} $1"
}

test_fail() {
    ((TESTS_FAILED++)) || true
    echo -e "${RED}✗ FAIL:${NC} $1"
    if [[ -n "${2:-}" ]]; then
        echo -e "${RED}       Details: $2${NC}"
    fi
}

run_test() {
    ((TESTS_RUN++)) || true
    local test_name="$1"
    shift
    "$@"
}

# =============================================================================
# Test Cases
# =============================================================================

test_lib_sources_successfully() {
    # Source constants first (required by lib-common.sh patterns)
    if ! source "$CONSTANTS" 2>/dev/null; then
        test_fail "constants.sh could not be sourced"
        return
    fi
    
    if source "$LIB_COMMON" 2>/dev/null; then
        test_pass "lib-common.sh sources successfully"
    else
        test_fail "lib-common.sh could not be sourced"
    fi
}

test_colors_defined() {
    source "$CONSTANTS"
    source "$LIB_COMMON"
    
    local missing=""
    [[ -z "${RED:-}" ]] && missing="$missing RED"
    [[ -z "${GREEN:-}" ]] && missing="$missing GREEN"
    [[ -z "${YELLOW:-}" ]] && missing="$missing YELLOW"
    [[ -z "${BLUE:-}" ]] && missing="$missing BLUE"
    [[ -z "${NC:-}" ]] && missing="$missing NC"
    
    if [[ -z "$missing" ]]; then
        test_pass "All color variables are defined"
    else
        test_fail "Missing color variables:$missing"
    fi
}

test_counters_initialize_to_zero() {
    # Source in subshell to get fresh state
    local result
    result=$(bash -c "source '$CONSTANTS' && source '$LIB_COMMON' && echo \$TOTAL_CHECKS,\$PASSED_CHECKS,\$FAILED_CHECKS,\$WARNING_CHECKS" 2>/dev/null)
    
    if [[ "$result" == "0,0,0,0" ]]; then
        test_pass "Counters initialize to zero"
    else
        test_fail "Counters not initialized to zero" "Got: $result, Expected: 0,0,0,0"
    fi
}

test_check_pass_increments_counters() {
    local result
    result=$(bash -c "
        source '$CONSTANTS'
        source '$LIB_COMMON'
        check_pass 'test message' >/dev/null
        echo \$TOTAL_CHECKS,\$PASSED_CHECKS,\$FAILED_CHECKS,\$WARNING_CHECKS
    " 2>/dev/null)
    
    if [[ "$result" == "1,1,0,0" ]]; then
        test_pass "check_pass increments TOTAL_CHECKS and PASSED_CHECKS"
    else
        test_fail "check_pass counter mismatch" "Got: $result, Expected: 1,1,0,0"
    fi
}

test_check_fail_increments_counters() {
    local result
    result=$(bash -c "
        source '$CONSTANTS'
        source '$LIB_COMMON'
        check_fail 'test message' >/dev/null
        echo \$TOTAL_CHECKS,\$PASSED_CHECKS,\$FAILED_CHECKS,\$WARNING_CHECKS
    " 2>/dev/null)
    
    if [[ "$result" == "1,0,1,0" ]]; then
        test_pass "check_fail increments TOTAL_CHECKS and FAILED_CHECKS"
    else
        test_fail "check_fail counter mismatch" "Got: $result, Expected: 1,0,1,0"
    fi
}

test_check_fail_appends_to_failed_messages() {
    local result
    result=$(bash -c "
        source '$CONSTANTS'
        source '$LIB_COMMON'
        check_fail 'test error message' >/dev/null
        echo \${#FAILED_MESSAGES[@]}
    " 2>/dev/null)
    
    if [[ "$result" == "1" ]]; then
        test_pass "check_fail appends to FAILED_MESSAGES array"
    else
        test_fail "check_fail did not append to FAILED_MESSAGES" "Array length: $result, Expected: 1"
    fi
}

test_check_warn_increments_counters() {
    local result
    result=$(bash -c "
        source '$CONSTANTS'
        source '$LIB_COMMON'
        check_warn 'test message' >/dev/null
        echo \$TOTAL_CHECKS,\$PASSED_CHECKS,\$FAILED_CHECKS,\$WARNING_CHECKS
    " 2>/dev/null)
    
    if [[ "$result" == "1,0,0,1" ]]; then
        test_pass "check_warn increments TOTAL_CHECKS and WARNING_CHECKS"
    else
        test_fail "check_warn counter mismatch" "Got: $result, Expected: 1,0,0,1"
    fi
}

test_check_warn_appends_to_warning_messages() {
    local result
    result=$(bash -c "
        source '$CONSTANTS'
        source '$LIB_COMMON'
        check_warn 'test warning message' >/dev/null
        echo \${#WARNING_MESSAGES[@]}
    " 2>/dev/null)
    
    if [[ "$result" == "1" ]]; then
        test_pass "check_warn appends to WARNING_MESSAGES array"
    else
        test_fail "check_warn did not append to WARNING_MESSAGES" "Array length: $result, Expected: 1"
    fi
}

test_section_header_produces_output() {
    local output
    output=$(bash -c "
        source '$CONSTANTS'
        source '$LIB_COMMON'
        section_header 'Test Section'
    " 2>/dev/null)
    
    if [[ "$output" == *"Test Section"* ]]; then
        test_pass "section_header produces output with section name"
    else
        test_fail "section_header did not produce expected output"
    fi
}

test_detect_cluster_cli_function_exists() {
    local result
    result=$(bash -c "
        source '$CONSTANTS'
        source '$LIB_COMMON'
        type detect_cluster_cli >/dev/null 2>&1 && echo 'exists' || echo 'missing'
    " 2>/dev/null)
    
    if [[ "$result" == "exists" ]]; then
        test_pass "detect_cluster_cli function is defined"
    else
        test_fail "detect_cluster_cli function is not defined"
    fi
}

test_detect_cluster_cli_sets_cluster_cli_bin() {
    # This test checks that CLUSTER_CLI_BIN is set after calling detect_cluster_cli
    # It will be empty string if neither oc nor kubectl is found, or the binary name
    local result
    result=$(bash -c "
        source '$CONSTANTS'
        source '$LIB_COMMON'
        detect_cluster_cli >/dev/null 2>&1
        # Should have set CLUSTER_CLI_BIN to oc, kubectl, or empty
        if [[ -n \"\$CLUSTER_CLI_BIN\" ]] || [[ \$FAILED_CHECKS -gt 0 ]]; then
            echo 'ok'
        else
            echo 'fail'
        fi
    " 2>/dev/null)
    
    if [[ "$result" == "ok" ]]; then
        test_pass "detect_cluster_cli sets CLUSTER_CLI_BIN or records failure"
    else
        test_fail "detect_cluster_cli did not behave as expected"
    fi
}

test_print_summary_function_exists() {
    local result
    result=$(bash -c "
        source '$CONSTANTS'
        source '$LIB_COMMON'
        type print_summary >/dev/null 2>&1 && echo 'exists' || echo 'missing'
    " 2>/dev/null)
    
    if [[ "$result" == "exists" ]]; then
        test_pass "print_summary function is defined"
    else
        test_fail "print_summary function is not defined"
    fi
}

test_print_summary_preflight_mode() {
    local output
    output=$(bash -c "
        source '$CONSTANTS'
        source '$LIB_COMMON'
        check_pass 'test' >/dev/null
        print_summary 'preflight'
    " 2>/dev/null)
    
    if [[ "$output" == *"Validation Summary"* ]] && [[ "$output" == *"ready to proceed"* ]]; then
        test_pass "print_summary preflight mode shows correct messages"
    else
        test_fail "print_summary preflight mode missing expected text"
    fi
}

test_print_summary_postflight_mode() {
    local output
    output=$(bash -c "
        source '$CONSTANTS'
        source '$LIB_COMMON'
        check_pass 'test' >/dev/null
        print_summary 'postflight'
    " 2>/dev/null)
    
    if [[ "$output" == *"Verification Summary"* ]] && [[ "$output" == *"completed successfully"* ]]; then
        test_pass "print_summary postflight mode shows correct messages"
    else
        test_fail "print_summary postflight mode missing expected text"
    fi
}

test_print_summary_returns_success_when_no_failures() {
    local result
    result=$(bash -c "
        source '$CONSTANTS'
        source '$LIB_COMMON'
        check_pass 'test' >/dev/null
        print_summary 'preflight' >/dev/null && echo 'success' || echo 'failure'
    " 2>/dev/null)
    
    if [[ "$result" == "success" ]]; then
        test_pass "print_summary returns 0 when no failures"
    else
        test_fail "print_summary returned non-zero when there were no failures"
    fi
}

test_print_summary_returns_failure_when_has_failures() {
    local result
    result=$(bash -c "
        source '$CONSTANTS'
        source '$LIB_COMMON'
        check_fail 'test' >/dev/null
        print_summary 'preflight' >/dev/null && echo 'success' || echo 'failure'
    " 2>/dev/null)
    
    if [[ "$result" == "failure" ]]; then
        test_pass "print_summary returns 1 when there are failures"
    else
        test_fail "print_summary returned 0 when there were failures"
    fi
}

test_exit_codes_defined() {
    source "$CONSTANTS"
    
    local missing=""
    [[ -z "${EXIT_SUCCESS:-}" ]] && missing="$missing EXIT_SUCCESS"
    [[ -z "${EXIT_FAILURE:-}" ]] && missing="$missing EXIT_FAILURE"
    [[ -z "${EXIT_INVALID_ARGS:-}" ]] && missing="$missing EXIT_INVALID_ARGS"
    
    if [[ -z "$missing" ]]; then
        test_pass "All exit code constants are defined in constants.sh"
    else
        test_fail "Missing exit code constants:$missing"
    fi
}

test_exit_codes_values() {
    source "$CONSTANTS"
    
    if [[ "$EXIT_SUCCESS" == "0" ]] && [[ "$EXIT_FAILURE" == "1" ]] && [[ "$EXIT_INVALID_ARGS" == "2" ]]; then
        test_pass "Exit code values are correct (0, 1, 2)"
    else
        test_fail "Exit code values incorrect" "Got: $EXIT_SUCCESS, $EXIT_FAILURE, $EXIT_INVALID_ARGS"
    fi
}

test_multiple_sourcing_prevented() {
    local result
    result=$(bash -c "
        source '$CONSTANTS'
        source '$LIB_COMMON'
        TOTAL_CHECKS=99
        source '$LIB_COMMON'
        echo \$TOTAL_CHECKS
    " 2>/dev/null)
    
    if [[ "$result" == "99" ]]; then
        test_pass "Multiple sourcing is prevented (counters not reset)"
    else
        test_fail "Multiple sourcing reset state" "TOTAL_CHECKS was reset to: $result"
    fi
}

# =============================================================================
# Main test runner
# =============================================================================

echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║   lib-common.sh Unit Tests                                ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

# Check that test files exist
if [[ ! -f "$LIB_COMMON" ]]; then
    echo -e "${RED}Error: lib-common.sh not found at $LIB_COMMON${NC}"
    exit 1
fi

if [[ ! -f "$CONSTANTS" ]]; then
    echo -e "${RED}Error: constants.sh not found at $CONSTANTS${NC}"
    exit 1
fi

echo "Testing: $LIB_COMMON"
echo ""

# Run all tests
run_test "lib sources successfully" test_lib_sources_successfully
run_test "colors defined" test_colors_defined
run_test "counters initialize to zero" test_counters_initialize_to_zero
run_test "check_pass increments counters" test_check_pass_increments_counters
run_test "check_fail increments counters" test_check_fail_increments_counters
run_test "check_fail appends to array" test_check_fail_appends_to_failed_messages
run_test "check_warn increments counters" test_check_warn_increments_counters
run_test "check_warn appends to array" test_check_warn_appends_to_warning_messages
run_test "section_header produces output" test_section_header_produces_output
run_test "detect_cluster_cli exists" test_detect_cluster_cli_function_exists
run_test "detect_cluster_cli sets bin" test_detect_cluster_cli_sets_cluster_cli_bin
run_test "print_summary exists" test_print_summary_function_exists
run_test "print_summary preflight mode" test_print_summary_preflight_mode
run_test "print_summary postflight mode" test_print_summary_postflight_mode
run_test "print_summary success return" test_print_summary_returns_success_when_no_failures
run_test "print_summary failure return" test_print_summary_returns_failure_when_has_failures
run_test "exit codes defined" test_exit_codes_defined
run_test "exit codes values" test_exit_codes_values
run_test "multiple sourcing prevented" test_multiple_sourcing_prevented

# Summary
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Tests Run:    $TESTS_RUN"
echo -e "${GREEN}Passed:       $TESTS_PASSED${NC}"
echo -e "${RED}Failed:       $TESTS_FAILED${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [[ $TESTS_FAILED -eq 0 ]]; then
    echo -e "${GREEN}✓ All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}✗ $TESTS_FAILED test(s) failed${NC}"
    exit 1
fi
