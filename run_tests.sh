#!/bin/bash
# Test runner script for ACM Switchover Automation
# Runs all tests with coverage and generates reports

set -e

echo "======================================"
echo "ACM Switchover - Test Suite"
echo "======================================"
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

STRICT_QUALITY="${STRICT_QUALITY:-0}"
QUALITY_PATHS=(acm_switchover.py lib/ modules/)

run_advisory_or_strict() {
    local label="$1"
    shift

    if "$@"; then
        return 0
    fi

    if [ "$STRICT_QUALITY" = "1" ]; then
        echo -e "${RED}${label} failed and STRICT_QUALITY=1${NC}"
        return 1
    fi

    echo -e "${YELLOW}${label} reported issues (advisory; set STRICT_QUALITY=1 to fail).${NC}"
    return 0
}

# Use existing virtual environment if active, else prefer .venv then venv
if [ -n "$VIRTUAL_ENV" ]; then
    echo -e "${GREEN}Using active virtualenv: $VIRTUAL_ENV${NC}"
elif [ -d ".venv" ]; then
    echo -e "${GREEN}Activating .venv${NC}"
    source .venv/bin/activate
elif [ -d "venv" ]; then
    echo -e "${GREEN}Activating venv${NC}"
    source venv/bin/activate
else
    echo -e "${YELLOW}No virtualenv found. Creating .venv...${NC}"
    python3 -m venv .venv
    source .venv/bin/activate
fi

# Install dependencies
echo -e "${GREEN}Installing dependencies...${NC}"
pip install -q --upgrade pip
pip install -q -r requirements.txt
pip install -q -r requirements-dev.txt

echo ""
echo "======================================"
echo "Running Unit Tests"
echo "======================================"

# E2E tests are on-demand. Set RUN_E2E=1 to include them.
# Main test run always excludes E2E; E2E runs separately when requested
pytest_args=(tests/ -v --cov=. --cov-report=term-missing --cov-report=html --cov-report=xml -m "not e2e")
python -m pytest "${pytest_args[@]}"

if [ "${RUN_E2E:-0}" = "1" ]; then
    echo ""
    echo "======================================"
    echo "Running E2E Tests (On Demand)"
    echo "======================================"
    python -m pytest tests/e2e/ -v -m e2e --cov=. --cov-append --cov-report=term-missing
fi

echo ""
echo "======================================"
echo "Running Code Quality Checks"
echo "======================================"

echo ""
echo "--- Flake8 (Style Check) ---"
flake8 "${QUALITY_PATHS[@]}" --count --select=E9,F63,F7,F82 --show-source --statistics
run_advisory_or_strict "Flake8 full style check" flake8 "${QUALITY_PATHS[@]}"

echo ""
echo "--- Pylint (Code Analysis) ---"
run_advisory_or_strict "Pylint" pylint "${QUALITY_PATHS[@]}" --max-line-length=120 --disable=C0103,C0114,C0115,C0116

echo ""
echo "--- Black (Format Check) ---"
run_advisory_or_strict "Black format check" black --check --line-length 120 "${QUALITY_PATHS[@]}"

echo ""
echo "--- isort (Import Sort Check) ---"
run_advisory_or_strict "isort import check" isort --check-only --profile black --line-length 120 "${QUALITY_PATHS[@]}"

echo ""
echo "--- MyPy (Type Check) ---"
run_advisory_or_strict "MyPy" mypy "${QUALITY_PATHS[@]}" --ignore-missing-imports --no-strict-optional

echo ""
echo "======================================"
echo "Running Security Checks"
echo "======================================"

echo ""
echo "--- Bandit (Security Linter) ---"
run_advisory_or_strict "Bandit security check" bandit --ini .bandit -ll

echo ""
echo "--- pip-audit (Dependency Vulnerabilities) ---"
run_advisory_or_strict "pip-audit dependency check" pip-audit

echo ""
echo "======================================"
echo "Syntax Validation"
echo "======================================"
python -m py_compile acm_switchover.py
python -m py_compile lib/*.py
python -m py_compile modules/*.py
echo -e "${GREEN}✓ All Python files compile successfully${NC}"

echo ""
echo "======================================"
echo "Test Summary"
echo "======================================"
echo -e "${GREEN}✓ Unit tests completed${NC}"
echo -e "${GREEN}✓ Coverage report generated: htmlcov/index.html${NC}"
echo -e "${GREEN}✓ Code quality checks completed${NC}"
echo -e "${GREEN}✓ Security checks completed${NC}"
echo ""
echo "To view coverage report, run:"
echo "  open htmlcov/index.html  # macOS"
echo "  xdg-open htmlcov/index.html  # Linux"
echo ""
