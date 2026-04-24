#!/bin/bash
#
# Argo CD ACM-touching Applications: pause or resume auto-sync.
#
# Use this to prevent GitOps from overriding ACM switchover changes. Pause before
# switchover; resume only after Git/desired state has been updated for the target
# hub (otherwise resume can revert switchover changes).
#
# Usage:
#   ./scripts/argocd-manage.sh --context <kubecontext> --mode pause|resume [--state-file <path>] [--target acm] [--dry-run]
#
# Exit codes:
#   0 - success
#   1 - failure
#   2 - invalid arguments
#
# DEPRECATED: Use the Python CLI (--argocd-manage) or the Ansible collection
# (argocd_manage role) instead. This script will be removed in a future release.

set -euo pipefail

echo "⚠️  WARNING: argocd-manage.sh is deprecated. Use 'python acm_switchover.py --argocd-manage' or the Ansible argocd_manage role instead." >&2
echo "" >&2

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/constants.sh" ]]; then
    # shellcheck source=constants.sh
    source "${SCRIPT_DIR}/constants.sh"
else
    echo "Error: constants.sh not found in ${SCRIPT_DIR}" >&2
    exit 1
fi

# Don't source full lib-common (counters/check_*); we only need CLI detection and shared vars.
# We need CLUSTER_CLI_BIN and the ACM matching rules - define them here to keep script standalone
# and avoid pulling in check_pass/check_fail.
CLUSTER_CLI_BIN=""
detect_cli() {
    if command -v oc &>/dev/null; then
        CLUSTER_CLI_BIN="oc"
    elif command -v kubectl &>/dev/null; then
        CLUSTER_CLI_BIN="kubectl"
    else
        echo "Error: oc or kubectl required" >&2
        exit 1
    fi
}

# ACM namespace regex and kinds (must match lib/argocd.py).
# Uses "(-.*)" so any open-cluster-management-* sub-namespace matches, mirroring
# lib-common.sh prefix semantics (which has no trailing anchor on the group).
ARGOCD_ACM_NS_REGEX='^(open-cluster-management($|-.*)|open-cluster-management-backup$|open-cluster-management-observability$|open-cluster-management-global-set$|multicluster-engine$|local-cluster)$'
ARGOCD_ACM_KINDS_JSON='["MultiClusterHub","MultiClusterEngine","MultiClusterObservability","ManagedCluster","ManagedClusterSet","ManagedClusterSetBinding","Placement","PlacementBinding","Policy","PolicySet","BackupSchedule","Restore","DataProtectionApplication","ClusterDeployment"]'

# Annotation key for our pause marker
ARGOCD_PAUSED_BY_ANNOTATION="acm-switchover.argoproj.io/paused-by"

# Default state file (can be overridden with --state-file)
DEFAULT_STATE_FILE=".state/argocd-pause-state.json"

# -----------------------------------------------------------------------------
# Parse arguments
# -----------------------------------------------------------------------------
CONTEXT=""
MODE=""
TARGET="acm"
STATE_FILE=""
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case $1 in
        --context)
            [[ $# -lt 2 || "$2" == --* ]] && { echo "Error: --context requires a value" >&2; exit "$EXIT_INVALID_ARGS"; }
            CONTEXT="$2"
            shift 2
            ;;
        --mode)
            [[ $# -lt 2 || "$2" == --* ]] && { echo "Error: --mode requires a value" >&2; exit "$EXIT_INVALID_ARGS"; }
            MODE="$2"
            shift 2
            ;;
        --target)
            [[ $# -lt 2 || "$2" == --* ]] && { echo "Error: --target requires a value" >&2; exit "$EXIT_INVALID_ARGS"; }
            TARGET="$2"
            if [[ "$TARGET" != "acm" ]]; then
                echo "Error: unsupported --target value: $TARGET" >&2
                exit "$EXIT_INVALID_ARGS"
            fi
            shift 2
            ;;
        --state-file)
            [[ $# -lt 2 || "$2" == --* ]] && { echo "Error: --state-file requires a value" >&2; exit "$EXIT_INVALID_ARGS"; }
            STATE_FILE="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --help|-h)
            echo "Usage: $0 --context <kubecontext> --mode pause|resume [--state-file <path>] [--target acm] [--dry-run]"
            echo ""
            echo "Options:"
            echo "  --context       Kubernetes context (required)"
            echo "  --mode          pause | resume (required)"
            echo "  --state-file    Path to state JSON (default: ${DEFAULT_STATE_FILE})"
            echo "  --target        acm (default: only ACM-touching Applications)"
            echo "  --dry-run       Print planned actions without patching"
            exit "$EXIT_SUCCESS"
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit "$EXIT_INVALID_ARGS"
            ;;
    esac
done

if [[ -z "$CONTEXT" ]] || [[ -z "$MODE" ]]; then
    echo "Error: --context and --mode are required" >&2
    exit "$EXIT_INVALID_ARGS"
fi

if [[ "$MODE" != "pause" ]] && [[ "$MODE" != "resume" ]]; then
    echo "Error: --mode must be 'pause' or 'resume'" >&2
    exit "$EXIT_INVALID_ARGS"
fi

if [[ -z "$STATE_FILE" ]]; then
    STATE_FILE="$DEFAULT_STATE_FILE"
fi

detect_cli

# -----------------------------------------------------------------------------
# Get Application objects cluster-wide so operator installs also include watched
# namespaces outside the Argo CD control plane namespace.
# -----------------------------------------------------------------------------
is_not_found_error() {
    local stderr_text="$1"
    local lowered
    lowered=$(printf '%s' "$stderr_text" | tr '[:upper:]' '[:lower:]')
    [[ "$lowered" == *"the server doesn't have a resource type"* ]] \
        || [[ "$lowered" == *"error from server (notfound)"* ]] \
        || [[ "$lowered" == *" not found"* ]] \
        || [[ "$lowered" == not\ found* ]]
}

run_json_query() {
    local missing_value="$1"
    shift

    local stdout_file stderr_file stdout_text stderr_text status
    stdout_file=$(mktemp)
    stderr_file=$(mktemp)
    set +e
    "$@" >"$stdout_file" 2>"$stderr_file"
    status=$?
    set -e
    stdout_text=$(cat "$stdout_file")
    stderr_text=$(cat "$stderr_file")
    rm -f "$stdout_file" "$stderr_file"

    if [[ $status -eq 0 ]]; then
        if [[ -n "$stderr_text" ]]; then
            printf '%s\n' "$stderr_text" >&2
        fi
        printf '%s\n' "$stdout_text"
        return 0
    fi

    if is_not_found_error "$stderr_text"; then
        printf '%s\n' "$missing_value"
        return 0
    fi

    if [[ -n "$stderr_text" ]]; then
        printf '%s\n' "$stderr_text" >&2
    elif [[ -n "$stdout_text" ]]; then
        printf '%s\n' "$stdout_text" >&2
    fi
    return 1
}

get_applications_json() {
    run_json_query '{"items":[]}' \
        "$CLUSTER_CLI_BIN" --context="$CONTEXT" get applications.argoproj.io -A -o json
}

get_application_json() {
    local app_ns="$1"
    local app_name="$2"
    run_json_query "" \
        "$CLUSTER_CLI_BIN" --context="$CONTEXT" -n "$app_ns" get application.argoproj.io "$app_name" -o json
}

# Filter to ACM-touching apps from a JSON list of Application objects (items array)
# Output: one line per app "namespace\tname"
filter_acm_touching_apps() {
    local apps_json="$1"
    jq -r --arg ns_regex "$ARGOCD_ACM_NS_REGEX" --argjson kinds "$ARGOCD_ACM_KINDS_JSON" '
        (.items // []) | map(select(type=="object"))
        | .[]
        | . as $app
        | ($app.status.resources // [])
        | if type=="array" then . else [] end
        | map(select(type=="object") | select(has("kind")))
        | map(select(((.namespace // "") | test($ns_regex)) or (.kind as $k | ($kinds | index($k)))))
        | if length > 0 then "\($app.metadata.namespace)\t\($app.metadata.name)" else empty end
    ' <<<"$apps_json"
}

# -----------------------------------------------------------------------------
# Pause: remove spec.syncPolicy.automated, add marker annotation, save state
# -----------------------------------------------------------------------------
write_pause_state() {
    local run_id="$1"
    local paused_at="$2"
    local apps_array="$3"
    local tmp_file="${STATE_FILE}.tmp.$$"
    local existing_json="{}"

    if [[ -f "$STATE_FILE" ]]; then
        existing_json=$(cat "$STATE_FILE")
    fi

    if ! jq -n -c \
        --argjson existing "$existing_json" \
        --arg run_id "$run_id" \
        --arg context "$CONTEXT" \
        --arg paused_at "$paused_at" \
        --argjson apps "$apps_array" \
        '
        def app_key:
            "\(.namespace // "")/\(.name // "")";
        def merge_apps(existing; incoming):
            reduce ((existing // []) + (incoming // []))[] as $app
                ({}; .[$app | app_key] = $app)
            | [.[]];
        def normalize:
            if type != "object" then {}
            elif has("run_id") and has("context") then
                { (.context): { run_id: .run_id, paused_at: .paused_at, apps: (.apps // []) } }
            else
                .
            end;
        ($existing | normalize) as $state
        | ($state[$context] // {}) as $current
        | $state + {
            ($context): {
                run_id: ($current.run_id // $run_id),
                paused_at: ($current.paused_at // $paused_at),
                apps: merge_apps(($current.apps // []); $apps)
            }
        }' > "$tmp_file"; then
        echo "Error: Failed to generate state JSON" >&2
        rm -f "$tmp_file"
        return 1
    fi
    if ! mv "$tmp_file" "$STATE_FILE"; then
        echo "Error: Failed to write state file $STATE_FILE" >&2
        rm -f "$tmp_file"
        return 1
    fi
}

read_pause_state_entry() {
    if [[ ! -f "$STATE_FILE" ]]; then
        return 0
    fi

    jq -c --arg context "$CONTEXT" '
        if type == "object" and has("run_id") then
            .
        else
            .[$context] // empty
        end
    ' "$STATE_FILE" 2>/dev/null || true
}

run_pause() {
    local crd_json
    crd_json=$(run_json_query "" "$CLUSTER_CLI_BIN" --context="$CONTEXT" get crd applications.argoproj.io -o json 2>/dev/null) || {
        echo "Error: Failed to check Applications CRD (possible auth/permission issue)." >&2
        return 1
    }
    if [[ -z "$crd_json" ]]; then
        echo "Applications CRD not found; nothing to pause." >&2
        return 0
    fi

    local existing_entry run_id
    existing_entry="$(read_pause_state_entry)"
    run_id="$(date -u +%Y%m%d%H%M%S)-${RANDOM:-0}"
    local paused_at
    paused_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    if [[ -n "$existing_entry" ]]; then
        run_id="$(jq -r '.run_id // empty' <<<"$existing_entry")"
        paused_at="$(jq -r '.paused_at // empty' <<<"$existing_entry")"
        [[ -n "$run_id" ]] || run_id="$(date -u +%Y%m%d%H%M%S)-${RANDOM:-0}"
        [[ -n "$paused_at" ]] || paused_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    fi
    local state_dir
    state_dir="$(dirname "$STATE_FILE")"
    if [[ -n "$state_dir" ]] && [[ ! -d "$state_dir" ]]; then
        mkdir -p "$state_dir"
    fi

    local apps_array="[]"
    local count=0

    local apps_json
    if ! apps_json="$(get_applications_json)"; then
        return 1
    fi
    while IFS=$'\t' read -r app_ns app_name; do
        [[ -z "$app_name" ]] && continue
        local app_full
        if ! app_full="$(get_application_json "$app_ns" "$app_name")"; then
            return 1
        fi
        if [[ -z "$app_full" ]]; then
            echo "Warning: Application $app_ns/$app_name not found, skipping" >&2
            continue
        fi
        local original_sync_policy
        original_sync_policy=$(echo "$app_full" | jq -c '.spec.syncPolicy // {}' 2>/dev/null || echo "{}")
        local has_automated
        has_automated=$(echo "$app_full" | jq -r '.spec.syncPolicy.automated // empty' 2>/dev/null || true)
        if [[ -z "$has_automated" ]]; then
            echo "  Skip $app_ns/$app_name (no auto-sync)"
            continue
        fi
        if [[ $DRY_RUN -eq 1 ]]; then
            echo "  [DRY-RUN] Would pause $app_ns/$app_name"
            ((count++)) || true
            apps_array=$(jq -c --arg ns "$app_ns" --arg name "$app_name" --argjson sp "$original_sync_policy" \
                '. + [{"namespace":$ns,"name":$name,"original_sync_policy":$sp}]' <<<"$apps_array")
            continue
        fi
        local spec_patch patch_json
        spec_patch=$(echo "$original_sync_policy" | jq 'del(.automated)' 2>/dev/null || echo "{}")
        patch_json=$(jq -n -c --arg run_id "$run_id" --argjson sp "$spec_patch" \
            --arg ann "$ARGOCD_PAUSED_BY_ANNOTATION" \
            '{ "metadata": { "annotations": { ($ann): $run_id } }, "spec": { "syncPolicy": $sp } }')
        "$CLUSTER_CLI_BIN" --context="$CONTEXT" -n "$app_ns" patch application.argoproj.io "$app_name" --type=merge -p "$patch_json" &>/dev/null || {
            echo "Error: Failed to patch $app_ns/$app_name" >&2
            if [[ $count -gt 0 ]]; then
                write_pause_state "$run_id" "$paused_at" "$apps_array"
                echo "Partial state written to $STATE_FILE (run_id=$run_id)." >&2
            fi
            return 1
        }
        echo "  Paused $app_ns/$app_name"
        ((count++)) || true
        apps_array=$(jq -c --arg ns "$app_ns" --arg name "$app_name" --argjson sp "$original_sync_policy" \
            '. + [{"namespace":$ns,"name":$name,"original_sync_policy":$sp}]' <<<"$apps_array")
    done < <(filter_acm_touching_apps "$apps_json")

    if [[ $count -eq 0 ]]; then
        echo "No ACM-touching Applications with auto-sync found to pause."
        return 0
    fi

    if [[ $DRY_RUN -eq 0 ]]; then
        write_pause_state "$run_id" "$paused_at" "$apps_array"
        echo "State written to $STATE_FILE (run_id=$run_id). Resume only after Git is updated for the target hub."
    else
        echo "[DRY-RUN] Would write state for $count app(s) to $STATE_FILE"
    fi
    return 0
}

# -----------------------------------------------------------------------------
# Resume: restore original syncPolicy from state file (only when marker matches)
# -----------------------------------------------------------------------------
run_resume() {
    if [[ ! -f "$STATE_FILE" ]]; then
        echo "Error: State file not found: $STATE_FILE" >&2
        return 1
    fi
    local state_entry run_id state_context
    state_entry=$(jq -c --arg context "$CONTEXT" '
        if type == "object" and has("run_id") then
            .
        else
            .[$context] // empty
        end
    ' "$STATE_FILE" 2>/dev/null || true)
    if [[ -z "$state_entry" ]]; then
        echo "Error: No state entry found for context '$CONTEXT' in $STATE_FILE" >&2
        return 1
    fi
    run_id=$(jq -r '.run_id // empty' <<<"$state_entry")
    state_context=$(jq -r '.context // empty' <<<"$state_entry")
    if [[ -z "$run_id" ]]; then
        echo "Error: Invalid state file (missing run_id)" >&2
        return 1
    fi
    if [[ -n "$state_context" ]] && [[ "$state_context" != "$CONTEXT" ]]; then
        echo "Warning: State file context '$state_context' != current --context '$CONTEXT'. Proceeding anyway."
    fi
    echo "Resuming from state file (run_id=$run_id). Only use after Git/desired state is updated for the target hub."
    local count=0
    local patch_failures=0
    while read -r line; do
        local app_ns app_name original_sync_policy
        app_ns=$(jq -r '.namespace' <<<"$line")
        app_name=$(jq -r '.name' <<<"$line")
        original_sync_policy=$(jq -c '.original_sync_policy' <<<"$line")
        [[ "$app_ns" == "null" || "$app_name" == "null" ]] && continue
        local current
        if ! current="$(get_application_json "$app_ns" "$app_name")"; then
            return 1
        fi
        if [[ -z "$current" ]]; then
            echo "  Skip $app_ns/$app_name (not found)"
            continue
        fi
        local current_marker
        current_marker=$(echo "$current" | jq -r '.metadata.annotations["'"$ARGOCD_PAUSED_BY_ANNOTATION"'"] // empty')
        if [[ "$current_marker" != "$run_id" ]]; then
            echo "  Skip $app_ns/$app_name (marker mismatch or not paused by this run)"
            continue
        fi
        if [[ $DRY_RUN -eq 1 ]]; then
            echo "  [DRY-RUN] Would resume $app_ns/$app_name"
            ((count++)) || true
            continue
        fi
        # Restore spec.syncPolicy and remove our annotation
        local patch_json
        patch_json=$(jq -n -c --argjson sp "$original_sync_policy" \
            '{ "metadata": { "annotations": { ("'"$ARGOCD_PAUSED_BY_ANNOTATION"'"): null } }, "spec": { "syncPolicy": $sp } }')
        if "$CLUSTER_CLI_BIN" --context="$CONTEXT" -n "$app_ns" patch application.argoproj.io "$app_name" --type=merge -p "$patch_json" &>/dev/null; then
            echo "  Resumed $app_ns/$app_name"
            ((count++)) || true
        else
            echo "  Error: Failed to patch $app_ns/$app_name" >&2
            ((patch_failures++)) || true
        fi
    done < <(jq -c '.apps[]?' <<<"$state_entry" 2>/dev/null || true)
    if [[ $count -eq 0 ]] && [[ $DRY_RUN -eq 0 ]]; then
        echo "No applications were resumed (none matched the state file run_id)."
    fi
    if [[ $patch_failures -gt 0 ]]; then
        echo "Resume completed with $patch_failures patch failure(s)." >&2
        return 1
    fi
    return 0
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
echo "Argo CD ACM-touching apps: mode=$MODE context=$CONTEXT state-file=$STATE_FILE target=$TARGET"
if [[ $DRY_RUN -eq 1 ]]; then
    echo "[DRY-RUN] No changes will be made."
fi
if [[ "$MODE" == "pause" ]]; then
    run_pause || exit 1
else
    run_resume || exit 1
fi
