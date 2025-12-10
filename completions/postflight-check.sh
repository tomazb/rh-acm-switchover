# Bash completion for postflight-check.sh
# shellcheck shell=bash

source "${BASH_SOURCE[0]%/*}/_acm_completion_lib.sh"

_postflight_check_complete() {
    local cur prev
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    case "$prev" in
        --new-hub-context|--old-hub-context)
            _acm_complete_from_list "$(_acm_get_contexts)"
            return
            ;;
    esac

    if [[ "$cur" == --new-hub-context=* ]]; then
        local value="${cur#*=}" prefix="--new-hub-context="
        COMPREPLY=( $(compgen -W "$(_acm_get_contexts)" -- "$value") )
        COMPREPLY=( "${COMPREPLY[@]/#/${prefix}}" )
        return
    fi
    if [[ "$cur" == --old-hub-context=* ]]; then
        local value="${cur#*=}" prefix="--old-hub-context="
        COMPREPLY=( $(compgen -W "$(_acm_get_contexts)" -- "$value") )
        COMPREPLY=( "${COMPREPLY[@]/#/${prefix}}" )
        return
    fi

    if [[ "$cur" == -* ]]; then
        local opts="--new-hub-context --old-hub-context --help -h"
        _acm_complete_from_list "$opts"
        return
    fi
}

complete -F _postflight_check_complete postflight-check.sh
