# Bash completion for preflight-check.sh
# shellcheck shell=bash

source "${BASH_SOURCE[0]%/*}/_acm_completion_lib.sh"

_preflight_check_complete() {
    local cur prev
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    case "$prev" in
        --primary-context|--secondary-context)
            _acm_complete_from_list "$(_acm_get_contexts)"
            return
            ;;
        --method)
            _acm_complete_from_list "passive full"
            return
            ;;
    esac

    if [[ "$cur" == --primary-context=* ]]; then
        local value="${cur#*=}" prefix="--primary-context="
        COMPREPLY=( $(compgen -W "$(_acm_get_contexts)" -- "$value") )
        COMPREPLY=( "${COMPREPLY[@]/#/${prefix}}" )
        return
    fi
    if [[ "$cur" == --secondary-context=* ]]; then
        local value="${cur#*=}" prefix="--secondary-context="
        COMPREPLY=( $(compgen -W "$(_acm_get_contexts)" -- "$value") )
        COMPREPLY=( "${COMPREPLY[@]/#/${prefix}}" )
        return
    fi
    if [[ "$cur" == --method=* ]]; then
        local value="${cur#*=}" prefix="--method="
        COMPREPLY=( $(compgen -W "passive full" -- "$value") )
        COMPREPLY=( "${COMPREPLY[@]/#/${prefix}}" )
        return
    fi

    if [[ "$cur" == -* ]]; then
        local opts="--primary-context --secondary-context --method --help -h"
        _acm_complete_from_list "$opts"
        return
    fi
}

complete -F _preflight_check_complete preflight-check.sh
