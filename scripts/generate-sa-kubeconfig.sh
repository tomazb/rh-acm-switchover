#!/bin/bash
#
# Generate kubeconfig for a Kubernetes service account
#
# This script generates a kubeconfig file that can be used to authenticate
# as a specific service account. The generated kubeconfig uses a short-lived
# token (48 hours by default) created via `kubectl create token`.
#
# Usage:
#   ./generate-sa-kubeconfig.sh [OPTIONS] <namespace> <service-account-name>
#
# Options:
#   --context <context>       - Kubernetes context to use (default: current context)
#   --user <name>             - Custom user name in kubeconfig (default: <context>-<sa-name>)
#   --token-duration <dur>    - Token validity duration (default: 48h)
#   -h, --help                - Show this help message
#
# Arguments:
#   namespace                 - Namespace where the service account exists
#   service-account           - Name of the service account
#
# Examples:
#   # Generate kubeconfig for operator service account (current context)
#   ./generate-sa-kubeconfig.sh acm-switchover acm-switchover-operator > operator-kubeconfig.yaml
#
#   # Generate kubeconfig with custom token duration
#   ./generate-sa-kubeconfig.sh --token-duration 8h acm-switchover acm-switchover-operator > kubeconfig.yaml
#
#   # Generate kubeconfig using a specific context with custom user name
#   ./generate-sa-kubeconfig.sh --context prod-hub --user prod-operator acm-switchover acm-switchover-operator > kubeconfig.yaml
#
#   # Generate unique user names for merging kubeconfigs from multiple clusters
#   ./generate-sa-kubeconfig.sh --context hub1 --user hub1-operator acm-switchover acm-switchover-operator > hub1.yaml
#   ./generate-sa-kubeconfig.sh --context hub2 --user hub2-operator acm-switchover acm-switchover-operator > hub2.yaml
#
# Prerequisites:
#   - kubectl configured with cluster access
#   - Service account must exist in the target namespace
#   - User must have permission to create tokens for the service account
#
# Note:
#   When merging kubeconfigs from multiple clusters, use unique --user names to
#   prevent credential collisions. The default pattern (<context>-<sa-name>) is
#   designed to be unique across clusters.
#

set -euo pipefail

# Parse optional flags first
CONTEXT=""
USER_NAME=""
DURATION="48h"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --context)
            # Validate that a context value is provided and doesn't look like a flag
            if [[ -n "${2:-}" && "${2:0:1}" != "-" ]]; then
                CONTEXT="${2}"
                shift 2
            else
                echo "Error: --context requires a value (context name)" >&2
                echo "" >&2
                echo "Usage: $0 [OPTIONS] <namespace> <service-account-name>" >&2
                exit 1
            fi
            ;;
        --user)
            # Validate that a user name value is provided and doesn't look like a flag
            if [[ -n "${2:-}" && "${2:0:1}" != "-" ]]; then
                USER_NAME="${2}"
                shift 2
            else
                echo "Error: --user requires a value (user name)" >&2
                echo "" >&2
                echo "Usage: $0 [OPTIONS] <namespace> <service-account-name>" >&2
                exit 1
            fi
            ;;
        --token-duration)
            # Validate that a duration value is provided and doesn't look like a flag
            if [[ -n "${2:-}" && "${2:0:1}" != "-" ]]; then
                DURATION="${2}"
                shift 2
            else
                echo "Error: --token-duration requires a value (e.g., 24h, 48h)" >&2
                echo "" >&2
                echo "Usage: $0 [OPTIONS] <namespace> <service-account-name>" >&2
                exit 1
            fi
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS] <namespace> <service-account-name>"
            echo ""
            echo "Options:"
            echo "  --context <context>       - Kubernetes context to use (default: current context)"
            echo "  --user <name>             - Custom user name in kubeconfig (default: <context>-<sa-name>)"
            echo "  --token-duration <dur>    - Token validity duration (default: 48h)"
            echo "  -h, --help                - Show this help message"
            echo ""
            echo "Arguments:"
            echo "  namespace                 - Namespace where the service account exists"
            echo "  service-account           - Name of the service account"
            echo ""
            echo "Examples:"
            echo "  $0 acm-switchover acm-switchover-operator > kubeconfig.yaml"
            echo "  $0 --context prod-hub --user prod-operator acm-switchover acm-switchover-operator > kubeconfig.yaml"
            echo "  $0 --token-duration 8h acm-switchover acm-switchover-operator > kubeconfig.yaml"
            echo ""
            echo "Note:"
            echo "  When merging kubeconfigs from multiple clusters, use unique --user names"
            echo "  to prevent credential collisions."
            exit 0
            ;;
        -*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            # End of flags, remaining args are positional
            break
            ;;
    esac
done

# Parse positional arguments
NAMESPACE="${1:-}"
SA_NAME="${2:-}"

# Build kubectl context args
KUBECTL_CONTEXT_ARGS=""
if [[ -n "$CONTEXT" ]]; then
    KUBECTL_CONTEXT_ARGS="--context=$CONTEXT"
fi

# Validate required arguments
if [[ -z "$NAMESPACE" || -z "$SA_NAME" ]]; then
    echo "Usage: $0 [OPTIONS] <namespace> <service-account-name>" >&2
    echo "" >&2
    echo "Options:" >&2
    echo "  --context <context>       - Kubernetes context to use (default: current context)" >&2
    echo "  --user <name>             - Custom user name in kubeconfig (default: <context>-<sa-name>)" >&2
    echo "  --token-duration <dur>    - Token validity duration (default: 48h)" >&2
    echo "" >&2
    echo "Arguments:" >&2
    echo "  namespace                 - Namespace where the service account exists" >&2
    echo "  service-account           - Name of the service account" >&2
    echo "" >&2
    echo "Examples:" >&2
    echo "  $0 acm-switchover acm-switchover-operator > kubeconfig.yaml" >&2
    echo "  $0 --context prod-hub --user prod-operator acm-switchover acm-switchover-operator > kubeconfig.yaml" >&2
    exit 1
fi

# Check if service account exists
# shellcheck disable=SC2086
if ! kubectl $KUBECTL_CONTEXT_ARGS get serviceaccount "$SA_NAME" -n "$NAMESPACE" &>/dev/null; then
    echo "Error: Service account '$SA_NAME' not found in namespace '$NAMESPACE'" >&2
    exit 1
fi

# Get cluster info from specified or current context
if [[ -n "$CONTEXT" ]]; then
    CLUSTER_NAME=$(kubectl config view -o jsonpath="{.contexts[?(@.name=='$CONTEXT')].context.cluster}")
    SERVER=$(kubectl config view -o jsonpath="{.clusters[?(@.name=='$CLUSTER_NAME')].cluster.server}")
    CA_DATA=$(kubectl config view --raw -o jsonpath="{.clusters[?(@.name=='$CLUSTER_NAME')].cluster.certificate-authority-data}")
else
    CLUSTER_NAME=$(kubectl config view --minify -o jsonpath='{.clusters[0].name}')
    SERVER=$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')
    CA_DATA=$(kubectl config view --minify --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')
fi

# Validate cluster info
if [[ -z "$SERVER" ]]; then
    echo "Error: Could not determine cluster server URL from context" >&2
    exit 1
fi

# Resolve user name: use provided --user, or default to <context>-<sa-name> pattern
# This pattern ensures unique user names when merging kubeconfigs from multiple clusters
if [[ -z "$USER_NAME" ]]; then
    # Use context name if specified, otherwise use cluster name
    if [[ -n "$CONTEXT" ]]; then
        USER_NAME="${CONTEXT}-${SA_NAME}"
    else
        USER_NAME="${CLUSTER_NAME}-${SA_NAME}"
    fi
fi

# Create a descriptive context name using the user name
CONTEXT_NAME="${USER_NAME}@${CLUSTER_NAME}"

# Generate token
# shellcheck disable=SC2086
TOKEN=$(kubectl $KUBECTL_CONTEXT_ARGS create token "$SA_NAME" -n "$NAMESPACE" --duration="$DURATION")

if [[ -z "$TOKEN" ]]; then
    echo "Error: Failed to generate token for service account" >&2
    exit 1
fi

# Output token expiration info to stderr (not captured in kubeconfig output)
echo "Generated kubeconfig with token valid for ${DURATION}" >&2
echo "  User name: ${USER_NAME}" >&2
echo "  Context: ${CONTEXT_NAME}" >&2

# Generate kubeconfig
cat <<KUBECONFIG
apiVersion: v1
kind: Config
clusters:
- cluster:
    certificate-authority-data: ${CA_DATA}
    server: ${SERVER}
  name: ${CLUSTER_NAME}
contexts:
- context:
    cluster: ${CLUSTER_NAME}
    namespace: ${NAMESPACE}
    user: ${USER_NAME}
  name: ${CONTEXT_NAME}
current-context: ${CONTEXT_NAME}
users:
- name: ${USER_NAME}
  user:
    token: ${TOKEN}
KUBECONFIG
