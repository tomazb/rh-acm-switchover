#!/bin/bash
#
# validate-versions.sh - Validate version consistency across all sources
#
# Exit code 0: All versions match
# Exit code 1: Version mismatch detected

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Read expected version from authoritative source
EXPECTED_VERSION="$(cat "${SCRIPT_DIR}/VERSION" | tr -d '[:space:]')"
EXPECTED_DATE="$(cat "${SCRIPT_DIR}/VERSION_DATE" | tr -d '[:space:]')"

echo "=== ACM Switchover Version Validation ==="
echo "Expected version: ${EXPECTED_VERSION}"
echo "Expected date:    ${EXPECTED_DATE}"
echo ""

ERRORS=0

# Function to check version in a file
check_version() {
    local file="$1"
    local pattern="$2"
    local expected="$3"
    local description="$4"
    
    if [[ ! -f "${file}" ]]; then
        echo "⚠ Skipped (not found): ${description}"
        return 0
    fi
    
    local actual
    actual=$(grep -oP "${pattern}" "${file}" 2>/dev/null | head -1 || echo "")
    
    if [[ -z "${actual}" ]]; then
        echo "⚠ Pattern not found: ${description}"
        return 0
    fi
    
    if [[ "${actual}" == "${expected}" ]]; then
        echo "✓ ${description}: ${actual}"
    else
        echo "✗ ${description}: expected '${expected}', got '${actual}'"
        ERRORS=$((ERRORS + 1))
    fi
}

# 1. lib/_version.py (lightweight version module)
check_version "${REPO_ROOT}/lib/_version.py" \
    '(?<=__version__ = ")[^"]+' \
    "${EXPECTED_VERSION}" \
    "lib/_version.py __version__"

check_version "${REPO_ROOT}/lib/_version.py" \
    '(?<=__version_date__ = ")[^"]+' \
    "${EXPECTED_DATE}" \
    "lib/_version.py __version_date__"

# 2. scripts/constants.sh
check_version "${REPO_ROOT}/scripts/constants.sh" \
    '(?<=SCRIPT_VERSION=")[^"]+' \
    "${EXPECTED_VERSION}" \
    "scripts/constants.sh SCRIPT_VERSION"

check_version "${REPO_ROOT}/scripts/constants.sh" \
    '(?<=SCRIPT_VERSION_DATE=")[^"]+' \
    "${EXPECTED_DATE}" \
    "scripts/constants.sh SCRIPT_VERSION_DATE"

# 3. setup.cfg
check_version "${REPO_ROOT}/setup.cfg" \
    '(?<=version = )[^\s]+' \
    "${EXPECTED_VERSION}" \
    "setup.cfg version"

# 4. container-bootstrap/Containerfile
check_version "${REPO_ROOT}/container-bootstrap/Containerfile" \
    '(?<=version=")[^"]+' \
    "${EXPECTED_VERSION}" \
    "Containerfile version label"

# 5. packaging/helm/acm-switchover/Chart.yaml (if exists)
HELM_CHART="${REPO_ROOT}/packaging/helm/acm-switchover/Chart.yaml"
if [[ -f "${HELM_CHART}" ]]; then
    check_version "${HELM_CHART}" \
        '(?<=^version: )[^\s]+' \
        "${EXPECTED_VERSION}" \
        "Helm chart version"
    
    check_version "${HELM_CHART}" \
        '(?<=^appVersion: ")[^"]+' \
        "${EXPECTED_VERSION}" \
        "Helm chart appVersion"
fi

# 6. deploy/helm/acm-switchover-rbac/Chart.yaml
RBAC_CHART="${REPO_ROOT}/deploy/helm/acm-switchover-rbac/Chart.yaml"
if [[ -f "${RBAC_CHART}" ]]; then
    check_version "${RBAC_CHART}" \
        '(?<=^version: )[^\s]+' \
        "${EXPECTED_VERSION}" \
        "RBAC Helm chart version"
    
    check_version "${RBAC_CHART}" \
        '(?<=^appVersion: ")[^"]+' \
        "${EXPECTED_VERSION}" \
        "RBAC Helm chart appVersion"
fi

echo ""
if [[ ${ERRORS} -eq 0 ]]; then
    echo "=== All versions are consistent ✓ ==="
    exit 0
else
    echo "=== ${ERRORS} version mismatch(es) detected ✗ ==="
    echo ""
    echo "Run './packaging/common/version-bump.sh ${EXPECTED_VERSION} ${EXPECTED_DATE}' to fix."
    exit 1
fi
