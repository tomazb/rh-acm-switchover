#!/usr/bin/env bash
# shellcheck shell=bash
# Shared helpers for ACM Switchover bash completions.
# Features:
# - CLI autodetection (prefers oc, falls back to kubectl)
# - 60s context cache with one refresh per TTL window
# - Small helpers for generating completion lists

# TTL for caching kubeconfig contexts (seconds). Override with ACM_CONTEXT_CACHE_TTL.
_ACM_CONTEXT_CACHE_TTL="${ACM_CONTEXT_CACHE_TTL:-60}"
_ACM_COMPLETION_CACHE_DIR="${XDG_CACHE_HOME:-/tmp}/acm_switchover_completion"
mkdir -p "${_ACM_COMPLETION_CACHE_DIR}" 2>/dev/null || true

# Detect cluster CLI. Exports ACM_CLI_BIN and ACM_CLI_NAME.
_acm_detect_cli() {
    if [[ -n "${ACM_CLI_BIN:-}" ]] && command -v "$ACM_CLI_BIN" >/dev/null 2>&1; then
        return 0
    fi

    if command -v oc >/dev/null 2>&1; then
        ACM_CLI_BIN="oc"
        ACM_CLI_NAME="OpenShift CLI"
        return 0
    fi

    if command -v kubectl >/dev/null 2>&1; then
        ACM_CLI_BIN="kubectl"
        ACM_CLI_NAME="Kubernetes CLI"
        return 0
    fi

    return 1
}

# Refresh contexts cache synchronously.
_acm_refresh_context_cache() {
    local cli="$1" cache_file="$2"
    local tmp_file
    tmp_file="${cache_file}.tmp"

    if ! command -v "$cli" >/dev/null 2>&1; then
        return 1
    fi

    "$cli" config get-contexts -o name 2>/dev/null | sort -u >"${tmp_file}" || true
    if [[ -s "${tmp_file}" ]]; then
        mv "${tmp_file}" "${cache_file}"
    else
        rm -f "${tmp_file}"
    fi
}

# Get contexts with 60s TTL cache. Spawns background refresh when stale.
_acm_get_contexts() {
    if ! _acm_detect_cli; then
        return 0
    fi

    local cache_file
    cache_file="${_ACM_COMPLETION_CACHE_DIR}/contexts_${ACM_CLI_BIN}.txt"
    local now mtime age
    now=$(date +%s)
    mtime=$(stat -c %Y "${cache_file}" 2>/dev/null || echo 0)
    age=$((now - mtime))

    # If cache missing, refresh synchronously once.
    if [[ ! -f "${cache_file}" ]]; then
        _acm_refresh_context_cache "$ACM_CLI_BIN" "${cache_file}"
    fi

    # If stale, refresh in background once per TTL window using lock dir.
    if (( age >= _ACM_CONTEXT_CACHE_TTL )); then
        local lock
        lock="${cache_file}.lock"
        if mkdir "${lock}" 2>/dev/null; then
            (
                _acm_refresh_context_cache "$ACM_CLI_BIN" "${cache_file}" || true
                rmdir "${lock}" 2>/dev/null || true
            ) &
        fi
    fi

    [[ -f "${cache_file}" ]] && cat "${cache_file}"
}

# Complete from a whitespace-separated list.
_acm_complete_from_list() {
    local list="$1" cur
    cur="${COMP_WORDS[COMP_CWORD]}"
    COMPREPLY=( $(compgen -W "$list" -- "$cur") )
}

# Complete file paths (for state files, etc.).
_acm_complete_files() {
    local cur
    cur="${COMP_WORDS[COMP_CWORD]}"
    COMPREPLY=( $(compgen -f -- "$cur") )
}
