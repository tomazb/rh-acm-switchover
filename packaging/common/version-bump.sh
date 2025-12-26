#!/bin/bash
#
# version-bump.sh - Update version across all sources
#
# Usage: ./version-bump.sh [NEW_VERSION] [NEW_DATE]
#
# If no arguments provided, reads current version from VERSION files.
# If NEW_DATE is not provided, uses today's date.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Read current version if not provided
NEW_VERSION="${1:-$(cat "${SCRIPT_DIR}/VERSION" | tr -d '[:space:]')}"
NEW_DATE="${2:-$(date +%Y-%m-%d)}"

echo "=== ACM Switchover Version Bump ==="
echo "Version: ${NEW_VERSION}"
echo "Date:    ${NEW_DATE}"
echo ""

# Cross-platform sed -i helper (works on macOS and Linux)
safe_sed() {
    local pattern="$1"
    local file="$2"
    if [[ "$(uname)" == "Darwin" ]]; then
        sed -i '' "$pattern" "$file"
    else
        sed -i "$pattern" "$file"
    fi
}

# Function to update a file with sed
update_file() {
    local file="$1"
    local pattern="$2"
    local replacement="$3"
    
    if [[ -f "${file}" ]]; then
        if safe_sed "s|${pattern}|${replacement}|g" "${file}"; then
            echo "✓ Updated: ${file}"
        else
            echo "✗ Failed: ${file}"
            return 1
        fi
    else
        echo "⚠ Skipped (not found): ${file}"
    fi
}

# 1. Update packaging/common/VERSION and VERSION_DATE
echo "${NEW_VERSION}" > "${SCRIPT_DIR}/VERSION"
echo "✓ Updated: packaging/common/VERSION"

echo "${NEW_DATE}" > "${SCRIPT_DIR}/VERSION_DATE"
echo "✓ Updated: packaging/common/VERSION_DATE"

# 2. Update lib/_version.py (lightweight version module)
update_file "${REPO_ROOT}/lib/_version.py" \
    '^__version__ = ".*"$' \
    "__version__ = \"${NEW_VERSION}\""

update_file "${REPO_ROOT}/lib/_version.py" \
    '^__version_date__ = ".*"$' \
    "__version_date__ = \"${NEW_DATE}\""

# 3. Update lib/__init__.py (for backward compatibility if version is still there)
update_file "${REPO_ROOT}/lib/__init__.py" \
    '^__version__ = ".*"$' \
    "__version__ = \"${NEW_VERSION}\""

update_file "${REPO_ROOT}/lib/__init__.py" \
    '^__version_date__ = ".*"$' \
    "__version_date__ = \"${NEW_DATE}\""

# 3. Update scripts/constants.sh
update_file "${REPO_ROOT}/scripts/constants.sh" \
    '^export SCRIPT_VERSION=".*"$' \
    "export SCRIPT_VERSION=\"${NEW_VERSION}\""

update_file "${REPO_ROOT}/scripts/constants.sh" \
    '^export SCRIPT_VERSION_DATE=".*"$' \
    "export SCRIPT_VERSION_DATE=\"${NEW_DATE}\""

# 4. Update setup.cfg (version field only)
update_file "${REPO_ROOT}/setup.cfg" \
    '^version = .*$' \
    "version = ${NEW_VERSION}"

# 5. Update container-bootstrap/Containerfile label
update_file "${REPO_ROOT}/container-bootstrap/Containerfile" \
    'version="[^"]*"' \
    "version=\"${NEW_VERSION}\""

# 6. Update packaging/helm/acm-switchover/Chart.yaml (if exists)
HELM_CHART="${REPO_ROOT}/packaging/helm/acm-switchover/Chart.yaml"
if [[ -f "${HELM_CHART}" ]]; then
    update_file "${HELM_CHART}" \
        '^version: .*$' \
        "version: ${NEW_VERSION}"
    
    update_file "${HELM_CHART}" \
        '^appVersion: .*$' \
        "appVersion: \"${NEW_VERSION}\""
fi

# 7. Update README.md version if present
README="${REPO_ROOT}/README.md"
if [[ -f "${README}" ]]; then
    # Update version badge or header if pattern exists
    # Common pattern: "Version 1.x.x" or "v1.x.x"
    safe_sed "s/Version [0-9]*\.[0-9]*\.[0-9]*/Version ${NEW_VERSION}/g" "${README}" 2>/dev/null || true
    safe_sed "s/v[0-9]*\.[0-9]*\.[0-9]* ([0-9-]*)/v${NEW_VERSION} (${NEW_DATE})/g" "${README}" 2>/dev/null || true
    echo "✓ Updated: README.md (if patterns matched)"
fi

echo ""
echo "=== Version bump complete ==="
echo ""
echo "Run './packaging/common/validate-versions.sh' to verify consistency."
echo ""
echo "Don't forget to:"
echo "  1. Update CHANGELOG.md with release notes"
echo "  2. Commit changes: git commit -am 'Bump version to ${NEW_VERSION}'"
echo "  3. Tag release: git tag v${NEW_VERSION}"
