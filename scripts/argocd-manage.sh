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

set -euo pipefail

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

# ACM namespace regex and kinds (must match lib-common.sh and Python)
ARGOCD_ACM_NS_REGEX='^(open-cluster-management($|-)|open-cluster-management-backup$|open-cluster-management-observability$|open-cluster-management-global-set$|multicluster-engine$|local-cluster)$'
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
# Get list of Application namespaces: operator install (argocds.argoproj.io) or vanilla (all)
# -----------------------------------------------------------------------------
get_application_namespaces() {
    if "$CLUSTER_CLI_BIN" --context="$CONTEXT" get crd argocds.argoproj.io &>/dev/null; then
        "$CLUSTER_CLI_BIN" --context="$CONTEXT" get argocds.argoproj.io -A -o json 2>/dev/null | \
            jq -r '.items[]? | .metadata.namespace' 2>/dev/null | sort -u || true
    else
        # Vanilla: list namespaces that have at least one Application
        "$CLUSTER_CLI_BIN" --context="$CONTEXT" get applications.argoproj.io -A -o json 2>/dev/null | \
            jq -r '.items[]? | .metadata.namespace' 2>/dev/null | sort -u || true
    fi
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
run_pause() {
    if ! "$CLUSTER_CLI_BIN" --context="$CONTEXT" get crd applications.argoproj.io &>/dev/null; then
        echo "Applications CRD not found; nothing to pause." >&2
        return 0
    fi

    local run_id
    run_id="$(date -u +%Y%m%d%H%M%S)-${RANDOM:-0}"
    local state_dir
    state_dir="$(dirname "$STATE_FILE")"
    if [[ -n "$state_dir" ]] && [[ ! -d "$state_dir" ]]; then
        mkdir -p "$state_dir"
    fi

    local apps_array="[]"
    local count=0

    while IFS= read -r ns; do
        [[ -z "$ns" ]] && continue
        local apps_json
        apps_json=$("$CLUSTER_CLI_BIN" --context="$CONTEXT" -n "$ns" get applications.argoproj.io -o json 2>/dev/null || echo '{"items":[]}')
        while IFS=$'\t' read -r app_ns app_name; do
            [[ -z "$app_name" ]] && continue
            local app_full
            app_full=$("$CLUSTER_CLI_BIN" --context="$CONTEXT" -n "$app_ns" get application.argoproj.io "$app_name" -o json 2>/dev/null || true)
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
                return 1
            }
            echo "  Paused $app_ns/$app_name"
            ((count++)) || true
            apps_array=$(jq -c --arg ns "$app_ns" --arg name "$app_name" --argjson sp "$original_sync_policy" \
                '. + [{"namespace":$ns,"name":$name,"original_sync_policy":$sp}]' <<<"$apps_array")
        done < <(filter_acm_touching_apps "$apps_json")
    done < <(get_application_namespaces)

    if [[ $count -eq 0 ]]; then
        echo "No ACM-touching Applications with auto-sync found to pause."
        return 0
    fi

    if [[ $DRY_RUN -eq 0 ]]; then
        jq -n -c \
            --arg run_id "$run_id" \
            --arg context "$CONTEXT" \
            --arg paused_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
            --argjson apps "$apps_array" \
            '{ run_id: $run_id, context: $context, paused_at: $paused_at, apps: $apps }' > "$STATE_FILE"
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
        exit 1
    fi
    local run_id state_context
    run_id=$(jq -r '.run_id // empty' "$STATE_FILE")
    state_context=$(jq -r '.context // empty' "$STATE_FILE")
    if [[ -z "$run_id" ]]; then
        echo "Error: Invalid state file (missing run_id)" >&2
        exit 1
    fi
    if [[ "$state_context" != "$CONTEXT" ]]; then
        echo "Warning: State file context '$state_context' != current --context '$CONTEXT'. Proceeding anyway."
    fi
    echo "Resuming from state file (run_id=$run_id). Only use after Git/desired state is updated for the target hub."
    local count=0
    while read -r line; do
        local app_ns app_name original_sync_policy
        app_ns=$(jq -r '.namespace' <<<"$line")
        app_name=$(jq -r '.name' <<<"$line")
        original_sync_policy=$(jq -c '.original_sync_policy' <<<"$line")
        [[ "$app_ns" == "null" || "$app_name" == "null" ]] && continue
        local current
        current=$("$CLUSTER_CLI_BIN" --context="$CONTEXT" -n "$app_ns" get application.argoproj.io "$app_name" -o json 2>/dev/null || true)
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
        fi
    done < <(jq -c '.apps[]?' "$STATE_FILE" 2>/dev/null || true)
    if [[ $count -eq 0 ]] && [[ $DRY_RUN -eq 0 ]]; then
        echo "No applications were resumed (none matched the state file run_id)."
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
    run_pause
else
    run_resume
fi
