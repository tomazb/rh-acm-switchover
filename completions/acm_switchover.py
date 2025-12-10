# Bash completion for acm_switchover.py
# shellcheck shell=bash

source "${BASH_SOURCE[0]%/*}/_acm_completion_lib.sh"

_acm_switchover_complete() {
    local cur prev
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    # Value completions based on previous token
    case "$prev" in
        --primary-context|--secondary-context)
            _acm_complete_from_list "$(_acm_get_contexts)"
            return
            ;;
        --method)
            _acm_complete_from_list "passive full"
            return
            ;;
        --old-hub-action)
            _acm_complete_from_list "secondary decommission none"
            return
            ;;
        --log-format)
            _acm_complete_from_list "text json"
            return
            ;;
        --state-file)
            _acm_complete_files
            return
            ;;
    esac

    # Support --flag=value style for common options
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
    if [[ "$cur" == --old-hub-action=* ]]; then
        local value="${cur#*=}" prefix="--old-hub-action="
        COMPREPLY=( $(compgen -W "secondary decommission none" -- "$value") )
        COMPREPLY=( "${COMPREPLY[@]/#/${prefix}}" )
        return
    fi
    if [[ "$cur" == --log-format=* ]]; then
        local value="${cur#*=}" prefix="--log-format="
        COMPREPLY=( $(compgen -W "text json" -- "$value") )
        COMPREPLY=( "${COMPREPLY[@]/#/${prefix}}" )
        return
    fi

    # Option list completion
    if [[ "$cur" == -* ]]; then
        local opts="--primary-context --secondary-context --validate-only --dry-run --decommission --method --manage-auto-import-strategy --state-file --reset-state --old-hub-action --skip-observability-checks --skip-rbac-validation --non-interactive --verbose -v --log-format --help -h"
        _acm_complete_from_list "$opts"
        return
    fi
}

complete -F _acm_switchover_complete acm_switchover.py
