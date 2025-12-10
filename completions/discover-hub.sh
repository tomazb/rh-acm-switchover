# Bash completion for discover-hub.sh
# shellcheck shell=bash

source "${BASH_SOURCE[0]%/*}/_acm_completion_lib.sh"

_discover_hub_complete() {
    local cur prev
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    case "$prev" in
        --contexts)
            _acm_complete_from_list "$(_acm_get_contexts)"
            return
            ;;
        --timeout)
            # numeric timeout, no suggestions
            return
            ;;
    esac

    if [[ "$cur" == --contexts=* ]]; then
        local value="${cur#*=}" prefix="--contexts="
        COMPREPLY=( $(compgen -W "$(_acm_get_contexts)" -- "$value") )
        COMPREPLY=( "${COMPREPLY[@]/#/${prefix}}" )
        return
    fi

    if [[ "$cur" == -* ]]; then
        local opts="--auto --contexts --run --verbose -v --timeout --help -h"
        _acm_complete_from_list "$opts"
        return
    fi
}

complete -F _discover_hub_complete discover-hub.sh
