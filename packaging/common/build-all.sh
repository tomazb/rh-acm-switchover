#!/bin/bash
#
# Build all package formats
#
# Usage: ./build-all.sh [options]
#
# Options:
#   --python    Build Python wheel and sdist
#   --container Build container image
#   --rpm       Build RPM (requires rpmbuild)
#   --deb       Build DEB (requires dpkg-buildpackage)
#   --helm      Package Helm chart
#   --all       Build all formats (default)
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # info prints an informational message prefixed with a green [INFO] tag to stdout.

info() { echo -e "${GREEN}[INFO]${NC} $*"; }
#warn prints a warning message prefixed with "[WARN]" in yellow to stdout.
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
# error prints its arguments as an error message to stderr prefixed with a red "[ERROR]" tag.
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

VERSION=$(cat "$REPO_ROOT/packaging/common/VERSION" 2>/dev/null || echo "unknown")
BUILD_PYTHON=false
BUILD_CONTAINER=false
BUILD_RPM=false
BUILD_DEB=false
BUILD_HELM=false

# Parse arguments
if [[ $# -eq 0 ]] || [[ "$1" == "--all" ]]; then
    BUILD_PYTHON=true
    BUILD_CONTAINER=true
    BUILD_HELM=true
    # RPM and DEB require specific tooling, skip by default
else
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --python) BUILD_PYTHON=true ;;
            --container) BUILD_CONTAINER=true ;;
            --rpm) BUILD_RPM=true ;;
            --deb) BUILD_DEB=true ;;
            --helm) BUILD_HELM=true ;;
            --all)
                BUILD_PYTHON=true
                BUILD_CONTAINER=true
                BUILD_HELM=true
                ;;
            *)
                error "Unknown option: $1"
                exit 1
                ;;
        esac
        shift
    done
fi

info "Building ACM Switchover v$VERSION"
echo ""

# Create dist directory
mkdir -p "$REPO_ROOT/dist"

# Build Python packages
if $BUILD_PYTHON; then
    info "Building Python packages..."
    cd "$REPO_ROOT"
    if command -v python3 &>/dev/null; then
        python3 -m pip install --quiet build
        python3 -m build --outdir dist/
        info "Python packages built in dist/"
    else
        warn "Python3 not found, skipping Python build"
    fi
fi

# Build container image
if $BUILD_CONTAINER; then
    info "Building container image..."
    cd "$REPO_ROOT"
    if command -v podman &>/dev/null; then
        podman build -f container-bootstrap/Containerfile -t "acm-switchover:$VERSION" .
        info "Container image built: acm-switchover:$VERSION"
    elif command -v docker &>/dev/null; then
        docker build -f container-bootstrap/Containerfile -t "acm-switchover:$VERSION" .
        info "Container image built: acm-switchover:$VERSION"
    else
        warn "Neither podman nor docker found, skipping container build"
    fi
fi

# Build RPM
if $BUILD_RPM; then
    info "Building RPM package..."
    if command -v rpmbuild &>/dev/null; then
        cd "$REPO_ROOT"
        # Create source tarball
        git archive --prefix="acm-switchover-$VERSION/" -o "dist/acm-switchover-$VERSION.tar.gz" HEAD
        # Build SRPM
        rpmbuild -bs --define "_sourcedir $REPO_ROOT/dist" --define "_srcrpmdir $REPO_ROOT/dist" packaging/rpm/acm-switchover.spec
        info "SRPM built in dist/"
    else
        warn "rpmbuild not found, skipping RPM build"
    fi
fi

# Build DEB
if $BUILD_DEB; then
    info "Building DEB package..."
    if command -v dpkg-buildpackage &>/dev/null; then
        cd "$REPO_ROOT"
        dpkg-buildpackage -us -uc -b
        mv ../acm-switchover_*.deb dist/ 2>/dev/null || true
        info "DEB package built in dist/"
    else
        warn "dpkg-buildpackage not found, skipping DEB build"
    fi
fi

# Package Helm chart
if $BUILD_HELM; then
    info "Packaging Helm chart..."
    if command -v helm &>/dev/null; then
        cd "$REPO_ROOT"
        helm package packaging/helm/acm-switchover -d dist/
        info "Helm chart packaged in dist/"
    else
        warn "helm not found, skipping Helm packaging"
    fi
fi

echo ""
info "Build complete! Artifacts in dist/"
ls -la "$REPO_ROOT/dist/" 2>/dev/null || true