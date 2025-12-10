# Bash completion for check_rbac.py
# shellcheck shell=bash

source "${BASH_SOURCE[0]%/*}/_acm_completion_lib.sh"

_check_rbac_complete() {
    local cur prev
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    case "$prev" in
        --context|--primary-context|--secondary-context)
            _acm_complete_from_list "$(_acm_get_contexts)"
            return
            ;;
    esac

    if [[ "$cur" == --context=* ]]; then
        local value="${cur#*=}" prefix="--context="
        COMPREPLY=( $(compgen -W "$(_acm_get_contexts)" -- "$value") )
        COMPREPLY=( "${COMPREPLY[@]/#/${prefix}}" )
        return
    fi
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

    if [[ "$cur" == -* ]]; then
        local opts="--context --primary-context --secondary-context --include-decommission --skip-observability --verbose -v --help -h"
        _acm_complete_from_list "$opts"
        return
    fi
}

complete -F _check_rbac_complete check_rbac.py
