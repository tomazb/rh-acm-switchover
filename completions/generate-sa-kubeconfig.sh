# Bash completion for generate-sa-kubeconfig.sh
# shellcheck shell=bash

source "${BASH_SOURCE[0]%/*}/_acm_completion_lib.sh"

_generate_sa_kubeconfig_complete() {
    local cur prev
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    case "$prev" in
        --context)
            _acm_complete_from_list "$(_acm_get_contexts)"
            return
            ;;
        --kubeconfig)
            _acm_complete_files
            return
            ;;
        --user|--token-duration)
            return
            ;;
    esac

    if [[ "$cur" == --context=* ]]; then
        local value="${cur#*=}" prefix="--context="
        COMPREPLY=( $(compgen -W "$(_acm_get_contexts)" -- "$value") )
        COMPREPLY=( "${COMPREPLY[@]/#/${prefix}}" )
        return
    fi
    if [[ "$cur" == --kubeconfig=* ]]; then
        local value="${cur#*=}"
        COMPREPLY=( $(compgen -f -- "$value") )
        COMPREPLY=( "${COMPREPLY[@]/#/--kubeconfig=}" )
        return
    fi

    if [[ "$cur" == -* ]]; then
        local opts="--kubeconfig --context --user --token-duration --help -h"
        _acm_complete_from_list "$opts"
        return
    fi
}

complete -F _generate_sa_kubeconfig_complete generate-sa-kubeconfig.sh
