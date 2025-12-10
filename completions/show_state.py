# Bash completion for show_state.py
# shellcheck shell=bash

source "${BASH_SOURCE[0]%/*}/_acm_completion_lib.sh"

_show_state_complete() {
    local cur prev
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    case "$prev" in
        --state-file)
            _acm_complete_files
            return
            ;;
    esac

    if [[ "$cur" == -* ]]; then
        local opts="--list -l --json -j --no-color --help -h"
        _acm_complete_from_list "$opts"
        return
    fi

    # Positional: suggest state files in .state directory
    _acm_complete_files
}

complete -F _show_state_complete show_state.py
