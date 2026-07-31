# Bash completion for generate-merged-kubeconfig.sh
# shellcheck shell=bash

source "${BASH_SOURCE[0]%/*}/_acm_completion_lib.sh"

_generate_merged_kubeconfig_complete() {
    local cur prev
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    case "$prev" in
        --admin-kubeconfig|--output)
            _acm_complete_files
            return
            ;;
        --namespace|--token-duration)
            return
            ;;
    esac

    if [[ "$cur" == --admin-kubeconfig=* ]]; then
        local value="${cur#*=}"
        COMPREPLY=( $(compgen -f -- "$value") )
        COMPREPLY=( "${COMPREPLY[@]/#/--admin-kubeconfig=}" )
        return
    fi
    if [[ "$cur" == --output=* ]]; then
        local value="${cur#*=}"
        COMPREPLY=( $(compgen -f -- "$value") )
        COMPREPLY=( "${COMPREPLY[@]/#/--output=}" )
        return
    fi

    if [[ "$cur" == -* ]]; then
        local opts="--admin-kubeconfig --token-duration --output --namespace --managed-cluster --help -h"
        _acm_complete_from_list "$opts"
        return
    fi
}

complete -F _generate_merged_kubeconfig_complete generate-merged-kubeconfig.sh
