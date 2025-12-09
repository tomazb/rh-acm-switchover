#!/bin/bash
#
# Generate kubeconfig for a Kubernetes service account
#
# This script generates a kubeconfig file that can be used to authenticate
# as a specific service account. The generated kubeconfig uses a short-lived
# token (24 hours by default) created via `kubectl create token`.
#
# Usage:
#   ./generate-sa-kubeconfig.sh [--context <context>] <namespace> <service-account-name> [duration]
#
# Arguments:
#   --context <context> - Kubernetes context to use (optional, uses current context if not specified)
#   namespace           - Namespace where the service account exists
#   service-account     - Name of the service account
#   duration            - Token duration (default: 24h)
#
# Examples:
#   # Generate kubeconfig for operator service account (current context)
#   ./generate-sa-kubeconfig.sh acm-switchover acm-switchover-operator > operator-kubeconfig.yaml
#
#   # Generate kubeconfig with custom token duration
#   ./generate-sa-kubeconfig.sh acm-switchover acm-switchover-operator 8h > operator-kubeconfig.yaml
#
#   # Generate kubeconfig using a specific context
#   ./generate-sa-kubeconfig.sh --context prod-hub acm-switchover acm-switchover-operator > operator-kubeconfig.yaml
#
#   # Combine context and custom duration
#   ./generate-sa-kubeconfig.sh --context prod-hub acm-switchover acm-switchover-operator 8h > operator-kubeconfig.yaml
#
# Prerequisites:
#   - kubectl configured with cluster access
#   - Service account must exist in the target namespace
#   - User must have permission to create tokens for the service account
#

set -euo pipefail

# Parse optional flags first
CONTEXT=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --context)
            CONTEXT="${2:-}"
            shift 2
            ;;
        --help|-h)
            echo "Usage: $0 [--context <context>] <namespace> <service-account-name> [duration]"
            echo ""
            echo "Arguments:"
            echo "  --context <context> - Kubernetes context to use (optional)"
            echo "  namespace           - Namespace where the service account exists"
            echo "  service-account     - Name of the service account"
            echo "  duration            - Token duration (default: 24h)"
            echo ""
            echo "Examples:"
            echo "  $0 acm-switchover acm-switchover-operator > kubeconfig.yaml"
            echo "  $0 --context prod-hub acm-switchover acm-switchover-operator 8h > kubeconfig.yaml"
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
DURATION="${3:-24h}"

# Build kubectl context args
KUBECTL_CONTEXT_ARGS=""
if [[ -n "$CONTEXT" ]]; then
    KUBECTL_CONTEXT_ARGS="--context=$CONTEXT"
fi

# Validate required arguments
if [[ -z "$NAMESPACE" || -z "$SA_NAME" ]]; then
    echo "Usage: $0 [--context <context>] <namespace> <service-account-name> [duration]" >&2
    echo "" >&2
    echo "Arguments:" >&2
    echo "  --context <context> - Kubernetes context to use (optional)" >&2
    echo "  namespace           - Namespace where the service account exists" >&2
    echo "  service-account     - Name of the service account" >&2
    echo "  duration            - Token duration (default: 24h)" >&2
    echo "" >&2
    echo "Examples:" >&2
    echo "  $0 acm-switchover acm-switchover-operator > kubeconfig.yaml" >&2
    echo "  $0 --context prod-hub acm-switchover acm-switchover-operator 8h > kubeconfig.yaml" >&2
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

# Generate token
# shellcheck disable=SC2086
TOKEN=$(kubectl $KUBECTL_CONTEXT_ARGS create token "$SA_NAME" -n "$NAMESPACE" --duration="$DURATION")

if [[ -z "$TOKEN" ]]; then
    echo "Error: Failed to generate token for service account" >&2
    exit 1
fi

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
    user: ${SA_NAME}
  name: ${SA_NAME}@${CLUSTER_NAME}
current-context: ${SA_NAME}@${CLUSTER_NAME}
users:
- name: ${SA_NAME}
  user:
    token: ${TOKEN}
KUBECONFIG
