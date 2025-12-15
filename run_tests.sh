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
python -m pytest tests/ -v --cov=. --cov-report=term-missing --cov-report=html --cov-report=xml

echo ""
echo "======================================"
echo "Running Code Quality Checks"
echo "======================================"

echo ""
echo "--- Flake8 (Style Check) ---"
flake8 acm_switchover.py lib/ modules/ || true

echo ""
echo "--- Pylint (Code Analysis) ---"
pylint acm_switchover.py lib/ modules/ --exit-zero || true

echo ""
echo "--- Black (Format Check) ---"
black --check --line-length 120 acm_switchover.py lib/ modules/ || echo -e "${YELLOW}Format issues found. Run: black --line-length 120 .${NC}"

echo ""
echo "--- isort (Import Sort Check) ---"
isort --check-only --profile black --line-length 120 acm_switchover.py lib/ modules/ || echo -e "${YELLOW}Import sorting issues found. Run: isort --profile black --line-length 120 .${NC}"

echo ""
echo "--- MyPy (Type Check) ---"
mypy acm_switchover.py lib/ modules/ --ignore-missing-imports --no-strict-optional || true

echo ""
echo "======================================"
echo "Running Security Checks"
echo "======================================"

echo ""
echo "--- Bandit (Security Linter) ---"
bandit -r acm_switchover.py lib/ modules/ -ll || true

echo ""
echo "--- pip-audit (Dependency Vulnerabilities) ---"
pip-audit || true

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
