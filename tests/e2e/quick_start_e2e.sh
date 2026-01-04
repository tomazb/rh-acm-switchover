#!/bin/bash
# Quick Start Script for ACM Switchover E2E Testing
# Provides a simple interface to run comprehensive E2E tests
# Usage: ./quick_start_e2e.sh [--cycles <n>] [--primary <ctx>] [--secondary <ctx>]
#
# ┌──────────────────────────────────────────────────────────────────────┐
# │ ⚠️  DEPRECATION WARNING - THIS SCRIPT WILL BE REMOVED               │
# ├──────────────────────────────────────────────────────────────────────┤
# │ This bash script is DEPRECATED and will be removed in version 2.0   │
# │                                                                       │
# │ MIGRATE TO: pytest -m e2e tests/e2e/                                │
# │                                                                       │
# │ Benefits of pytest approach:                                         │
# │   • Native Python integration with full ACM API access              │
# │   • Soak testing with time limits and max failures                  │
# │   • Resume capability for long-running tests                        │
# │   • Real-time monitoring and metrics (JSONL)                        │
# │   • Better error handling and debugging                             │
# │                                                                       │
# │ See: tests/e2e/README.md for migration guide                        │
# └──────────────────────────────────────────────────────────────────────┘

set -euo pipefail

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source constants
if [[ -f "${SCRIPT_DIR}/../../scripts/constants.sh" ]]; then
    source "${SCRIPT_DIR}/../../scripts/constants.sh"
else
    echo "ERROR: Cannot find scripts/constants.sh" >&2
    exit 1
fi

# Default configuration
CYCLES=${CYCLES:-5}
PRIMARY_CONTEXT=${PRIMARY_CONTEXT:-mgmt1}
SECONDARY_CONTEXT=${SECONDARY_CONTEXT:-mgmt2}

# Colors for output
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m' # No Color

# =============================================================================
# Utility Functions
# =============================================================================

print_header() {
    echo -e "${BLUE}"
    echo "=================================================="
    echo "ACM Switchover E2E Testing - Quick Start"
    echo "=================================================="
    echo -e "${NC}"
    echo "Configuration:"
    echo "  Primary Hub: $PRIMARY_CONTEXT"
    echo "  Secondary Hub: $SECONDARY_CONTEXT"
    echo "  Test Cycles: $CYCLES"
    echo ""
}

print_usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --cycles <n>        Number of test cycles to run (default: 5)"
    echo "  --primary <ctx>     Primary hub context (default: mgmt1)"
    echo "  --secondary <ctx>   Secondary hub context (default: mgmt2)"
    echo "  --dry-run           Run validation only, no actual switchovers"
    echo "  --monitoring-only   Run monitoring only (no execution)"
    echo "  --analyze-only      Analyze existing results only"
    echo "  --results-dir <dir> Directory for existing results (with --analyze-only)"
    echo "  --help, -h          Show this help message"
    echo ""
    echo "Examples:"
    echo "  # Run 5 complete switchover cycles"
    echo "  $0 --cycles 5"
    echo ""
    echo "  # Run validation only (no changes)"
    echo "  $0 --dry-run --cycles 1"
    echo ""
    echo "  # Run monitoring only"
    echo "  $0 --monitoring-only"
    echo ""
    echo "  # Analyze existing results"
    echo "  $0 --analyze-only --results-dir ./e2e-results-20240101-120000"
    echo ""
}

log_message() {
    local level=${1:-INFO}
    local message=$2
    echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] [${level}] ${message}"
}

check_prerequisites() {
    log_message "INFO" "Checking prerequisites..."
    
    # Check if required tools are available
    local missing_tools=()
    
    if ! command -v kubectl &> /dev/null; then
        missing_tools+=("kubectl")
    fi
    
    if ! command -v python3 &> /dev/null; then
        missing_tools+=("python3")
    fi
    
    if ! command -v jq &> /dev/null; then
        missing_tools+=("jq")
    fi
    
    if [[ ${#missing_tools[@]} -gt 0 ]]; then
        log_message "ERROR" "Missing required tools: ${missing_tools[*]}"
        echo "Please install the missing tools and try again."
        return 1
    fi
    
    # Check if we're in the right directory
    if [[ ! -f "acm_switchover.py" ]]; then
        log_message "ERROR" "acm_switchover.py not found. Please run from the project root directory."
        return 1
    fi
    
    # Check if E2E test scripts exist
    if [[ ! -f "${SCRIPT_DIR}/e2e_test_orchestrator.sh" ]]; then
        log_message "ERROR" "E2E test orchestrator not found. Please ensure all E2E scripts are available."
        return 1
    fi
    
    log_message "INFO" "✅ Prerequisites check passed"
    return 0
}

validate_environment() {
    log_message "INFO" "Validating environment..."
    
    # Check contexts exist
    if ! kubectl config get-contexts "$PRIMARY_CONTEXT" &> /dev/null; then
        log_message "ERROR" "Primary context '$PRIMARY_CONTEXT' not found"
        echo ""
        echo "Available contexts:"
        kubectl config get-contexts -o name
        return 1
    fi
    
    if ! kubectl config get-contexts "$SECONDARY_CONTEXT" &> /dev/null; then
        log_message "ERROR" "Secondary context '$SECONDARY_CONTEXT' not found"
        echo ""
        echo "Available contexts:"
        kubectl config get-contexts -o name
        return 1
    fi
    
    # Test connectivity to both hubs
    log_message "INFO" "Testing connectivity to primary hub..."
    if ! kubectl --context "$PRIMARY_CONTEXT" cluster-info &> /dev/null; then
        log_message "ERROR" "Cannot connect to primary hub '$PRIMARY_CONTEXT'"
        return 1
    fi
    
    log_message "INFO" "Testing connectivity to secondary hub..."
    if ! kubectl --context "$SECONDARY_CONTEXT" cluster-info &> /dev/null; then
        log_message "ERROR" "Cannot connect to secondary hub '$SECONDARY_CONTEXT'"
        return 1
    fi
    
    # Check ACM installation
    log_message "INFO" "Checking ACM installation..."
    if ! kubectl --context "$PRIMARY_CONTEXT" get namespace "$ACM_NAMESPACE" &> /dev/null; then
        log_message "ERROR" "ACM not installed on primary hub"
        return 1
    fi
    
    if ! kubectl --context "$SECONDARY_CONTEXT" get namespace "$ACM_NAMESPACE" &> /dev/null; then
        log_message "ERROR" "ACM not installed on secondary hub"
        return 1
    fi
    
    # Check OADP installation
    log_message "INFO" "Checking OADP installation..."
    if ! kubectl --context "$PRIMARY_CONTEXT" get namespace "$BACKUP_NAMESPACE" &> /dev/null; then
        log_message "ERROR" "OADP not installed on primary hub"
        return 1
    fi
    
    if ! kubectl --context "$SECONDARY_CONTEXT" get namespace "$BACKUP_NAMESPACE" &> /dev/null; then
        log_message "ERROR" "OADP not installed on secondary hub"
        return 1
    fi
    
    # Get current state
    log_message "INFO" "Current environment state:"
    local primary_mc_count=$(kubectl --context "$PRIMARY_CONTEXT" get managedclusters --no-headers 2>/dev/null | grep -v "$LOCAL_CLUSTER_NAME" | wc -l || echo "0")
    local secondary_mc_count=$(kubectl --context "$SECONDARY_CONTEXT" get managedclusters --no-headers 2>/dev/null | grep -v "$LOCAL_CLUSTER_NAME" | wc -l || echo "0")
    
    echo "  Primary managed clusters: $primary_mc_count"
    echo "  Secondary managed clusters: $secondary_mc_count"
    
    log_message "INFO" "✅ Environment validation passed"
    return 0
}

# =============================================================================
# Test Execution Functions
# =============================================================================

run_dry_run_validation() {
    log_message "INFO" "Running dry-run validation..."
    
    # Run preflight validation
    log_message "INFO" "Running preflight checks..."
    if ! "${SCRIPT_DIR}/../../scripts/preflight-check.sh" \
        --primary-context "$PRIMARY_CONTEXT" \
        --secondary-context "$SECONDARY_CONTEXT" \
        --method passive; then
        log_message "ERROR" "Preflight validation failed"
        return 1
    fi
    
    # Run dry-run switchover
    log_message "INFO" "Running dry-run switchover..."
    if ! python acm_switchover.py \
        --primary-context "$PRIMARY_CONTEXT" \
        --secondary-context "$SECONDARY_CONTEXT" \
        --method passive \
        --old-hub-action secondary \
        --dry-run \
        --verbose; then
        log_message "ERROR" "Dry-run switchover failed"
        return 1
    fi
    
    log_message "INFO" "✅ Dry-run validation completed successfully"
    return 0
}

run_monitoring_only() {
    log_message "INFO" "Starting monitoring-only mode..."
    log_message "INFO" "Press Ctrl+C to stop monitoring"
    
    # Start phase monitor
    "${SCRIPT_DIR}/phase_monitor.sh" \
        --primary "$PRIMARY_CONTEXT" \
        --secondary "$SECONDARY_CONTEXT" \
        --phase "monitoring" \
        --output-dir "./monitoring-$(date +%Y%m%d-%H%M%S)"
}

run_full_e2e_test() {
    log_message "INFO" "Starting full E2E test suite..."
    log_message "INFO" "This will run $CYCLES complete switchover cycles"
    log_message "WARNING" "This will make actual changes to your clusters!"
    echo ""
    
    # Confirmation prompt
    if [[ $CYCLES -gt 1 ]]; then
        echo -e "${YELLOW}⚠️  WARNING: You are about to run $CYCLES consecutive switchover cycles.${NC}"
        echo -e "${YELLOW}   Each cycle will switch management between hubs and back.${NC}"
        echo -e "${YELLOW}   This will take approximately $((CYCLES * 45)) minutes to complete.${NC}"
        echo ""
    else
        echo -e "${YELLOW}⚠️  WARNING: You are about to execute a real switchover.${NC}"
        echo -e "${YELLOW}   This will switch cluster management from $PRIMARY_CONTEXT to $SECONDARY_CONTEXT.${NC}"
        echo ""
    fi
    
    read -p "Do you want to continue? (yes/no): " -r
    if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
        log_message "INFO" "Test cancelled by user"
        return 0
    fi
    
    # Run the E2E test orchestrator
    log_message "INFO" "Starting E2E test orchestrator..."
    
    PRIMARY_CONTEXT="$PRIMARY_CONTEXT" \
    SECONDARY_CONTEXT="$SECONDARY_CONTEXT" \
    CYCLES="$CYCLES" \
    "${SCRIPT_DIR}/e2e_test_orchestrator.sh"
    
    local exit_code=$?
    
    if [[ $exit_code -eq 0 ]]; then
        log_message "INFO" "✅ E2E test suite completed successfully"
        
        # Generate analysis report
        local results_dir=$(ls -td ./e2e-results-* 2>/dev/null | head -1)
        if [[ -n "$results_dir" ]]; then
            log_message "INFO" "Generating analysis report..."
            if python3 "${SCRIPT_DIR}/e2e_analyzer.py" \
                --results-dir "$results_dir" \
                --output "${results_dir}/analysis_report.html"; then
                log_message "INFO" "Analysis report generated: ${results_dir}/analysis_report.html"
            fi
        fi
    else
        log_message "ERROR" "❌ E2E test suite failed"
    fi
    
    return $exit_code
}

analyze_existing_results() {
    local results_dir="$1"
    
    log_message "INFO" "Analyzing existing results from: $results_dir"
    
    if [[ ! -d "$results_dir" ]]; then
        log_message "ERROR" "Results directory not found: $results_dir"
        return 1
    fi
    
    # Generate analysis report
    if python3 "${SCRIPT_DIR}/e2e_analyzer.py" \
        --results-dir "$results_dir" \
        --output "${results_dir}/analysis_report.html"; then
        log_message "INFO" "✅ Analysis completed"
        log_message "INFO" "Report available at: ${results_dir}/analysis_report.html"
        return 0
    else
        log_message "ERROR" "Analysis failed"
        return 1
    fi
}

# =============================================================================
# Deprecation Warning
# =============================================================================

print_deprecation_warning() {
    echo ""
    echo -e "${YELLOW}╔═══════════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${YELLOW}║  DEPRECATION WARNING                                                       ║${NC}"
    echo -e "${YELLOW}║                                                                             ║${NC}"
    echo -e "${YELLOW}║  This bash script is deprecated in favor of the Python E2E orchestrator.   ║${NC}"
    echo -e "${YELLOW}║  For CI/automated testing, use: pytest -m e2e tests/e2e/                   ║${NC}"
    echo -e "${YELLOW}║  For programmatic usage: from tests.e2e.orchestrator import E2EOrchestrator║${NC}"
    echo -e "${YELLOW}║                                                                             ║${NC}"
    echo -e "${YELLOW}║  This script will be removed in a future release.                          ║${NC}"
    echo -e "${YELLOW}╚═══════════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    sleep 2
}

# =============================================================================
# Main Execution
# =============================================================================

main() {
    # Print deprecation warning
    print_deprecation_warning
    
    # Parse command line arguments
    local dry_run=false
    local monitoring_only=false
    local analyze_only=false
    local results_dir=""
    
    while [[ $# -gt 0 ]]; do
        case $1 in
            --cycles)
                CYCLES="$2"
                shift 2
                ;;
            --primary)
                PRIMARY_CONTEXT="$2"
                shift 2
                ;;
            --secondary)
                SECONDARY_CONTEXT="$2"
                shift 2
                ;;
            --dry-run)
                dry_run=true
                shift
                ;;
            --monitoring-only)
                monitoring_only=true
                shift
                ;;
            --analyze-only)
                analyze_only=true
                shift
                ;;
            --results-dir)
                results_dir="$2"
                shift 2
                ;;
            --help|-h)
                print_usage
                exit 0
                ;;
            *)
                log_message "ERROR" "Unknown option: $1"
                print_usage
                exit 1
                ;;
        esac
    done
    
    # Print header
    print_header
    
    # Check prerequisites
    if ! check_prerequisites; then
        exit 1
    fi
    
    # Execute based on mode
    if [[ "$analyze_only" == "true" ]]; then
        if [[ -z "$results_dir" ]]; then
            log_message "ERROR" "--results-dir required with --analyze-only"
            exit 1
        fi
        analyze_existing_results "$results_dir"
    elif [[ "$monitoring_only" == "true" ]]; then
        if ! validate_environment; then
            exit 1
        fi
        run_monitoring_only
    elif [[ "$dry_run" == "true" ]]; then
        if ! validate_environment; then
            exit 1
        fi
        run_dry_run_validation
    else
        if ! validate_environment; then
            exit 1
        fi
        run_full_e2e_test
    fi
}

# Execute main function
main "$@"
