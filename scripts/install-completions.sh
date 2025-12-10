#!/bin/bash
# Install bash completions for ACM Switchover tools (supports oc and kubectl).
#
# Usage:
#   ./scripts/install-completions.sh [--system|--user]
#   ./scripts/install-completions.sh test-completion
#
# Flags:
#   --system   Install system-wide (requires root; uses /usr/share/bash-completion/completions)
#   --user     Install for current user (~/.local/share/bash-completion/completions)
#   --help|-h  Show this help message
#
# Notes:
#   - SELinux: restorecon is run automatically (if available and SELinux not Disabled); no context
#     arguments are needed because directory defaults handle labeling.
#   - Root is required for system-wide installs; otherwise a user-only install is performed.
#   - Context completion cache TTL is 60s; cache refresh is automatic.
#   - Use 'test-completion' to verify installed locations.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR%/scripts}"
SRC_DIR="${REPO_ROOT}/completions"
SYSTEM_DIR="/usr/share/bash-completion/completions"
USER_DIR="${HOME}/.local/share/bash-completion/completions"
MODE="auto"

print_help() {
    sed -n '1,25p' "$0"
    exit 0
}

selinux_enabled() {
    local status=""
    if command -v getenforce >/dev/null 2>&1; then
        status=$(getenforce 2>/dev/null || true)
    elif command -v sestatus >/dev/null 2>&1; then
        status=$(sestatus 2>/dev/null | awk -F: '/SELinux status:/ {gsub(/^[ 	]+/, "", $2); print $2}')
    fi
    [[ "$status" =~ Enforcing|Permissive ]]
}

maybe_restorecon() {
    local target_dir="$1"
    if selinux_enabled && command -v restorecon >/dev/null 2>&1; then
        restorecon "${target_dir}"/* >/dev/null 2>&1 || true
    fi
}

install_files() {
    local dest_dir="$1"
    mkdir -p "${dest_dir}"
    for f in "${SRC_DIR}"/*; do
        install -m 0644 "$f" "${dest_dir}/$(basename "$f")"
    done
    maybe_restorecon "${dest_dir}"
    echo "Installed completions to ${dest_dir}"
}

test_completion() {
    local found=0
    local locations=("${SYSTEM_DIR}" "${USER_DIR}")
    for dir in "${locations[@]}"; do
        if [[ -f "${dir}/acm_switchover.py" && -f "${dir}/_acm_completion_lib.sh" ]]; then
            echo "Found completions in ${dir}"
            found=1
        fi
    done
    if [[ $found -eq 0 ]]; then
        echo "No installed completions detected. Run ./scripts/install-completions.sh first." >&2
        exit 1
    fi
    exit 0
}

if [[ $# -gt 0 ]]; then
    case "$1" in
        --help|-h)
            print_help
            ;;
        --system)
            MODE="system"
            shift
            ;;
        --user)
            MODE="user"
            shift
            ;;
        test-completion)
            test_completion
            ;;
    esac
fi

if [[ ! -d "$SRC_DIR" ]]; then
    echo "Error: completions source directory not found at $SRC_DIR" >&2
    exit 1
fi

case "$MODE" in
    system)
        if [[ $EUID -ne 0 ]]; then
            echo "Error: --system install requires root" >&2
            exit 1
        fi
        install_files "$SYSTEM_DIR"
        ;;
    user)
        install_files "$USER_DIR"
        ;;
    auto)
        if [[ $EUID -eq 0 ]]; then
            install_files "$SYSTEM_DIR"
        else
            install_files "$USER_DIR"
        fi
        ;;
    *)
        echo "Unknown mode: $MODE" >&2
        exit 1
        ;;
esac

cat <<'EOF'

Next steps:
- Open a new shell or source your completion file (e.g., source /etc/bash_completion or ~/.bash_completion) to load completions.
- Run ./scripts/install-completions.sh test-completion to verify installation.
EOF
