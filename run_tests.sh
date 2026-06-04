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

# CI-equivalent quality gates fail by default. Set STRICT_QUALITY=0 for a local
# advisory-only quality pass while preserving test execution.
STRICT_QUALITY="${STRICT_QUALITY:-1}"
FLAKE8_JOBS="${FLAKE8_JOBS:-1}"
BLACK_WORKERS="${BLACK_WORKERS:-1}"
PYLINT_PATHS=(acm_switchover.py lib/ modules/)
QUALITY_PATHS=(
    acm_switchover.py
    lib
    modules
    ansible_collections/tomazb/acm_switchover/plugins
    ansible_collections/tomazb/acm_switchover/tests
    tests
)
MYPY_PATHS=(
    acm_switchover.py
    lib/
    modules/
    ansible_collections/tomazb/acm_switchover/plugins
    ansible_collections/tomazb/acm_switchover/tests
)

run_ci_quality_gate() {
    local label="$1"
    shift

    if "$@"; then
        return 0
    fi

    if [ "$STRICT_QUALITY" = "0" ]; then
        echo -e "${YELLOW}${label} reported issues (advisory because STRICT_QUALITY=0).${NC}"
        return 0
    fi

    echo -e "${RED}${label} failed. Set STRICT_QUALITY=0 only for an advisory local run.${NC}"
    return 1
}

run_advisory_check() {
    local label="$1"
    shift

    if "$@"; then
        return 0
    fi

    echo -e "${YELLOW}${label} reported issues (advisory, matching CI exit-zero behavior).${NC}"
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

# E2E tests are on-demand. Release-framework helper tests run as their own
# explicit lane so local verification matches CI structure.
pytest_args=(tests/ --ignore=tests/release -v --cov=. --cov-report=term-missing --cov-report=html --cov-report=xml -m "not e2e")
python -m pytest "${pytest_args[@]}"

echo ""
echo "======================================"
echo "Running Release Framework Tests"
echo "======================================"
python -m pytest tests/release -q

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
flake8 . --jobs "$FLAKE8_JOBS" --count --select=E9,F63,F7,F82 --show-source --statistics
run_advisory_check "Flake8 full style check" flake8 . --jobs "$FLAKE8_JOBS" --count --exit-zero --max-complexity=15 --max-line-length=120 --statistics

echo ""
echo "--- Pylint (Code Analysis) ---"
run_advisory_check "Pylint" pylint "${PYLINT_PATHS[@]}" --exit-zero --max-line-length=120 --disable=C0103,C0114,C0115,C0116

echo ""
echo "--- Black (Format Check) ---"
run_ci_quality_gate "Black format check" black --check --workers "$BLACK_WORKERS" --line-length 120 "${QUALITY_PATHS[@]}"

echo ""
echo "--- isort (Import Sort Check) ---"
run_ci_quality_gate "isort import check" isort --check-only --profile black --line-length 120 "${QUALITY_PATHS[@]}"

echo ""
echo "--- MyPy (Type Check) ---"
run_ci_quality_gate "MyPy" mypy --explicit-package-bases "${MYPY_PATHS[@]}" --ignore-missing-imports --no-strict-optional

echo ""
echo "======================================"
echo "Running Security Checks"
echo "======================================"

echo ""
echo "--- Bandit (Security Linter) ---"
bandit --ini .bandit -f json -o bandit-report.json || true
run_ci_quality_gate "Bandit security check" bandit --ini .bandit -f txt

echo ""
echo "--- pip-audit (Dependency Vulnerabilities) ---"
run_advisory_check "pip-audit dependency check" pip-audit

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
echo -e "${GREEN}✓ Release framework tests completed${NC}"
echo -e "${GREEN}✓ Coverage report generated: htmlcov/index.html${NC}"
echo -e "${GREEN}✓ Code quality checks completed${NC}"
echo -e "${GREEN}✓ Security checks completed${NC}"
echo ""
echo "To view coverage report, run:"
echo "  open htmlcov/index.html  # macOS"
echo "  xdg-open htmlcov/index.html  # Linux"
echo ""
