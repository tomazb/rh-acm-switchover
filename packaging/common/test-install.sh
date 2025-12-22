#!/bin/bash
#
# Test package installation in clean containers
#
# Usage: ./test-install.sh [format]
#
# Formats:
#   pip       Test pip installation
#   fedora    Test RPM installation on Fedora
#   ubuntu    Test DEB installation on Ubuntu
#   all       Test all formats (default)
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info() { echo -e "${GREEN}[INFO]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }
success() { echo -e "${GREEN}[PASS]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; }

# Detect container runtime
if command -v podman &>/dev/null; then
    CONTAINER_CMD="podman"
elif command -v docker &>/dev/null; then
    CONTAINER_CMD="docker"
else
    error "Neither podman nor docker found"
    exit 1
fi

VERSION=$(cat "$REPO_ROOT/packaging/common/VERSION" 2>/dev/null || echo "unknown")
IMAGE_TAG="acm-switchover:${VERSION}"

test_pip_install() {
    info "Testing pip installation in Python container..."
    
    $CONTAINER_CMD run --rm -v "$REPO_ROOT:/src:ro" python:3.11-slim sh -c '
        set -e
        cd /src
        pip install --quiet .
        
        # Test commands are available
        acm-switchover --version
        acm-switchover-rbac --version
        acm-switchover-state --version
        
        echo "All commands work!"
    '
    
    if [[ $? -eq 0 ]]; then
        success "pip installation test passed"
        return 0
    else
        fail "pip installation test failed"
        return 1
    fi
}

test_fedora_install() {
    info "Testing RPM installation on Fedora..."
    warn "RPM test requires built RPM package - skipping for now"
    return 0
}

test_ubuntu_install() {
    info "Testing DEB installation on Ubuntu..."
    warn "DEB test requires built DEB package - skipping for now"
    return 0
}

test_container_image() {
    info "Testing container image..."
    
    # Build image first
    $CONTAINER_CMD build -f "$REPO_ROOT/container-bootstrap/Containerfile" -t "${IMAGE_TAG}" "$REPO_ROOT"
    
    # Test commands
    $CONTAINER_CMD run --rm "${IMAGE_TAG}" --help >/dev/null
    $CONTAINER_CMD run --rm --entrypoint python3 "${IMAGE_TAG}" /app/check_rbac.py --help >/dev/null
    $CONTAINER_CMD run --rm --entrypoint python3 "${IMAGE_TAG}" /app/show_state.py --help >/dev/null
    
    if [[ $? -eq 0 ]]; then
        success "Container image test passed"
        return 0
    else
        fail "Container image test failed"
        return 1
    fi
}

# Main
TEST_FORMAT="${1:-all}"

case "$TEST_FORMAT" in
    pip)
        test_pip_install
        ;;
    fedora)
        test_fedora_install
        ;;
    ubuntu)
        test_ubuntu_install
        ;;
    container)
        test_container_image
        ;;
    all)
        test_pip_install
        test_container_image
        ;;
    *)
        error "Unknown format: $TEST_FORMAT"
        echo "Usage: $0 [pip|fedora|ubuntu|container|all]"
        exit 1
        ;;
esac

echo ""
info "Test completed!"
